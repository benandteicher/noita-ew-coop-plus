"""Independent edge review with the production RPC wrapper and companion code.

Engine calls, serialization, and native I/O are deterministic substitutes. This
does not establish native physics or actual Steam delivery.
"""
from pathlib import Path
import os
import unittest
from lupa.luajit21 import LuaRuntime
from tests.test_homunculus_lifecycle import FIXTURE

ROOT = Path(os.environ.get('CORE_AUDIT_BASELINE', Path(__file__).resolve().parents[1]))


class CompanionRpcReview(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(FIXTURE)
        self.lua.execute(r'''
remote_player = ctx.rpc_player_data
player_fns={peer_get_player_data=function(peer)
 assert(peer=='review-peer');return remote_player
end}
function dofile_once(path)
 if path:find('bitser.lua',1,true)then
  return {dumps=function(value)last_serialized=value;return 'review-payload'end}
 elseif path:find('hex.lua',1,true)then return {}
 elseif path:find('net_handling.lua',1,true)then return {mod={},proxy={}}
 else error('unexpected module '..path)end
end
ewext={netmanager_send=function(msg)
 if fail_send then error('review native send failure')end
 assert(msg:byte(1)==6,'companion snapshot must use reliable broadcast')
 assert(last_serialized.key==1)
 packets[#packets+1]=last_serialized.value
end}
''')
        self.lua.globals().net = self.lua.execute(
            (ROOT / 'quant.ew/files/core/net.lua').read_text(encoding='utf-8'))
        self.lua.globals().companion = self.lua.execute(
            (ROOT / 'quant.ew/files/system/homunculus/homunculus.lua').read_text(encoding='utf-8'))
        self.lua.execute('''
function receive(ho,lu,gh)
 net.net_handling.mod[1]('review-peer',{ho or{},lu or{},gh or{},frame})
 assert(ctx.rpc_peer_id==nil and ctx.rpc_player_data==nil)
end
function deliver_last()
 net.net_handling.mod[1]('review-peer',packets[#packets])
end
''')

    def test_production_rpc_failure_retry_and_duplicate_terminal_delivery(self):
        self.lua.execute('''
add(10,'homunculus',1);companion.on_world_update();deliver_last()
assert(entities[21].alive);EntityKill(10);fail_send=true
for i=1,3 do assert(not pcall(companion.on_world_update));frame=frame+1 end
assert(#packets==1 and entities[21].alive)
fail_send=false;companion.on_world_update();assert(#packets==2)
deliver_last();deliver_last();assert(not entities[21].alive)
for i=1,30 do frame=frame+1;companion.on_world_update()end
assert(#packets==2)
''')

    def test_local_body_replacement_consumes_terminal_once(self):
        self.lua.execute('''
add(10,'homunculus',1);companion.on_world_update();deliver_last()
ctx.my_player.entity=3;entities[1].alive=false;frame=frame+1
companion.on_world_update();assert(#packets==2);deliver_last()
assert(not entities[21].alive)
companion.on_world_update();assert(#packets==2)
add(11,'homunculus',3);companion.on_world_update();deliver_last()
assert(entities[22].alive and entities[22].x==entities[11].x)
''')

    def test_remote_body_replacement_before_cache_rescan(self):
        self.lua.execute('''
add(10,'lukki',2,111);receive({},{{1,2,222}},{})
remote_player.entity=3;entities[2].alive=false;frame=frame+1
receive({},{{3,4,333}},{})
assert(entities[10].alive and entities[10].physics==222)
assert(entities[21].alive and entities[21].physics==333)
receive({},{},{})
assert(not entities[21].alive and entities[10].alive)
''')

    def test_same_frame_terminal_then_regrowth_uses_live_lukki_cache(self):
        self.lua.execute('''
receive({},{{1,2,111}},{})
assert(entities[21].alive);receive({},{},{})
assert(not entities[21].alive)
receive({},{{3,4,222},{5,6,333}},{})
assert(entities[22].physics==222 and entities[23].physics==333)
receive({},{{7,8,444},{9,10,555}},{})
assert(next_id==23 and entities[22].physics==444 and entities[23].physics==555)
''')

    def test_terminal_minion_removal_does_not_remove_ghosts(self):
        self.lua.execute('''
add(10,'homunculus',1);add(11,'angry_ghost',1)
entities[11].rnd={value_float=0.25}
add(12,'angry_ghost',2);entities[12].rnd={value_float=0.5}
companion.on_world_update();deliver_last();assert(entities[21].alive)
EntityKill(10);companion.on_world_update();deliver_last()
assert(not entities[21].alive and entities[12].alive)
assert(entities[12].rnd.value_float==0.25)
companion.on_world_update();assert(#packets==3)
''')


if __name__ == '__main__':
    unittest.main(verbosity=2)
