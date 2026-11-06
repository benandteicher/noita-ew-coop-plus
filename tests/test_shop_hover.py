"""Production shop Lua in mocked peers; no native purchase/physics claim."""
from pathlib import Path
from collections import deque
import unittest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[1]
class ShopPeers:
    def __init__(self,enabled=True):
        self.q=deque();self.peers={}
        for name in ['A','B']:
            l=LuaRuntime(unpack_returned_tuples=True);l.globals().PEER=name
            l.globals().READ=lambda p:(ROOT/p.removeprefix('mods/')).read_text(encoding='utf-8')
            l.globals().SEND=lambda k,d,s=name:self.q.append((s,k,d))
            l.execute((ROOT/'tests/shop_fixture.lua').read_text());self.peers[name]=l
        self.a,self.b=self.peers.values()
        self.a.globals().settings['quant.ew.shop_hover']=enabled
        self.a.execute('shop.on_local_player_spawn()');self.flush()
    def flush(self):
        for _ in range(100):
            if not self.q:return
            s,k,d=self.q.popleft()
            for n,l in self.peers.items():
                if n!=s:l.globals().receive(s,k,d)
        raise AssertionError('RPC loop')
    def toggle(self,on):
        self.a.globals().settings['quant.ew.shop_hover']=on
        self.a.execute('shop.on_world_update()');self.flush()
        self.b.execute('shop.on_world_update()')

class ShopHoverTests(unittest.TestCase):
    def test_off_client_preferences_and_future_generation_only(self):
        p=ShopPeers(False)
        p.b.execute("settings['quant.ew.shop_hover']=true;create(1);mark(1);shop.on_world_update();assert(not item.marker(1))")
        p.toggle(True)
        p.b.execute("assert(not item.marker(1));create(2);mark(2);assert(EntityHasTag(2,item.tag));assert(not entities[2].components[3].enabled)")
        p.b.execute("entities[3]={tags={},components={}};mark(3);assert(not item.marker(3))")

    def test_suspension_serialization_and_disable_reenable_are_terminal(self):
        p=ShopPeers()
        p.b.execute('create(1);mark(1);clone(1,2);shop.on_world_update();assert(not entities[2].components[3].enabled);assert(not entities[2].components[4].enabled);assert(entities[2].components[2].cost==30 and entities[2].components[2].stealable)')
        p.toggle(False)
        p.b.execute('assert(entities[1].components[3].enabled and entities[1].components[4].enabled);assert(item.marker(1).value_bool)')
        p.toggle(True)
        p.b.execute('shop.on_world_update();assert(not EntityHasTag(1,item.tag));assert(entities[2].components[3].enabled);create(3);mark(3);assert(not entities[3].components[3].enabled)')

    def test_real_pickup_callback_releases_and_reconciles_stale_replica(self):
        p=ShopPeers()
        p.b.execute("create(1);mark(1);clone(1,2);entities[1].root=99;entities[1].components[1].has_been_picked_by_player=true;assert(loadstring(READ('mods/quant.ew/files/system/shop_hover/pickup.lua')))();CrossCall=function(k,...) cross[k](...) end;item_pickup(1,99,'spell');assert(item.marker(1).value_bool);assert(not EntityHasTag(1,item.tag))")
        p.flush()
        self.assertTrue(p.a.eval("decode(globals.ew_shop_hover_v1).released['0:10:1365']"))
        p.b.execute('shop.on_world_update();assert(not EntityHasTag(2,item.tag));assert(entities[2].components[3].enabled);entities[1].root=nil;entities[1].components[3].enabled=true;entities[1].components[4].enabled=true;shop.on_world_update();assert(entities[1].components[3].enabled)')
        p.a.execute("create(10);mark(10);assert(not item.marker(10))")

    def test_only_components_we_disabled_are_restored_and_loading_guard_is_respected(self):
        p=ShopPeers()
        p.a.execute("create(1,false,false);mark(1);item.release(1);assert(not entities[1].components[3].enabled and not entities[1].components[4].enabled);create(2);mark(2);EntityAddComponent2(2,'VariableStorageComponent',{name='ew_no_ground'});item.release(2);assert(not entities[2].components[3].enabled and entities[2].components[4].enabled)")
        p.a.execute("create(3);mark(3);entities[3].components[1].has_been_picked_by_player=true;shop.on_world_update();assert(EntityHasTag(3,item.tag))")

    def test_save_reload_off_retires_epoch_and_client_cannot_forge_settings(self):
        p=ShopPeers();p.b.execute('create(1);mark(1)')
        p.a.execute("receive('B','snapshot',encode({{enabled=false,epoch=99,released={}}}))")
        p.a.execute('shop.on_should_send_updates()');p.flush()
        p.b.execute('shop.on_world_update();assert(EntityHasTag(1,item.tag))')
        p.toggle(False)
        saved=p.a.eval('globals.ew_shop_hover_v1')
        q=ShopPeers(False);q.a.globals().globals.ew_shop_hover_v1=saved
        # Fresh module VM simulates Continue, rather than replacing active state.
        l=LuaRuntime(unpack_returned_tuples=True);l.globals().PEER='A';l.globals().READ=lambda p:(ROOT/p.removeprefix('mods/')).read_text()
        l.globals().SEND=lambda *a:None;l.execute((ROOT/'tests/shop_fixture.lua').read_text())
        l.globals().globals.ew_shop_hover_v1=saved
        l.execute('shop.on_local_player_spawn();assert(decode(globals.ew_shop_hover_v1).epoch==1)')

    def test_generation_wrapper_preserves_arguments_result_and_wands(self):
        p=ShopPeers()
        p.a.execute("assert(patched:find('ew_shop_hover_generate',1,true));generate_shop_item=function(x,y,...) args={x,y,...};return 42 end;CrossCall=function(k,...) called={k,...} end;assert(loadstring(READ('mods/quant.ew/files/system/shop_hover/generated.lua')))();assert(ew_shop_hover_generate(1,2,true,nil,true)==42);assert(args[1]==1 and args[2]==2 and args[3]==true and args[5]==true);assert(called[1]=='ew_shop_hover_generated' and called[2]==42)")

if __name__=='__main__':unittest.main(verbosity=2)
