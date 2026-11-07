"""Production ledger/adapter under simulated peers. Native checks reported separately."""
import unittest
from collections import deque
from lupa.luajit21 import LuaRuntime
from test_coop_revival import ROOT, Peers

class IncomePeers(Peers):
    def __init__(self, configure='', restored=None):
        self.q,self.peers=deque(),{}
        for name in ('A','B'):
            lua=LuaRuntime(unpack_returned_tuples=True)
            lua.globals().PEER=name
            lua.globals().READ=lambda p:(ROOT/p.removeprefix('mods/')).read_text(encoding='utf-8')
            lua.globals().SEND=lambda method,data,sender=name:self.q.append((sender,method,data))
            lua.execute((ROOT/'tests/coop_fixture.lua').read_text())
            lua.execute((ROOT/'tests/income_fixture.lua').read_text())
            for k,v in (restored or {}).get(name,{}).items():
                if not k.startswith('__'):lua.globals().globals[k]=v
            lua.execute(configure)
            snapshot=(restored or {}).get(name,{})
            if '__body' in snapshot:
                lua.globals().RESTORED_BODY=snapshot['__body']
                lua.execute('entities[ctx.my_player.entity]=decode(RESTORED_BODY)')
                lua.globals().flags.ew_flag_notplayer_active=snapshot['__body_dead']

            lua.execute('load_modules()')
            self.peers[name]=lua
        self.a,self.b=self.peers.values()
        self.a.execute('income.on_should_send_updates();coop.on_should_send_updates()');self.flush()
        self.b.execute('ctx.cap.coop_revival.dead_ready()');self.flush()
    def snapshot(self):
        # A native saved checkpoint includes the body wallet/receipt and all
        # paged Globals. Copying only two root strings is an invalid v2 fixture.
        out={}
        for name,lua in self.peers.items():
            out[name]={k:v for k,v in lua.globals().globals.items() if k.startswith('ew_income')}
            out[name]['__body']=lua.eval('encode(entities[ctx.my_player.entity])')
            out[name]['__body_dead']=lua.eval('flags.ew_flag_notplayer_active')
        return out
    def pickup(self,who='A',gid='42',value=10):
        # Registration and native callback substitute, not engine collection proof.
        for lua in self.peers.values():lua.execute(f"gold=nugget('{gid}',{value})")
        self.a.execute('frame=60;income.on_world_update()');self.flush()
        self.peers[who].execute("cross.ew_income_pickup(gold,ctx.my_player.entity)")
        self.flush();self.tick(35)
    def money(self,peer):return self.peers[peer].eval('entities[ctx.my_player.entity].c.WalletComponent.money')
    def ledger(self,expr):return self.a.eval("dofile_once('mods/quant.ew/files/system/shared_income/persistence.lua').read('ew_income_run_v1')"+expr)

class IncomeTests(unittest.TestCase):
    def test_default_off_and_client_cannot_enable(self):
        p=IncomePeers("if PEER=='A'then settings['quant.ew.shared_income']=false end")
        self.assertFalse(p.a.eval('ctx.cap.shared_income.enabled()'))
        self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertFalse(p.b.eval('cross.ew_income_pickup(nugget("42",10),ctx.my_player.entity)'))

    def test_income_independent_of_cooperative_revival(self):
        p=IncomePeers('settings.enabled=false')
        p.pickup();self.assertEqual((p.money('A'),p.money('B')),(110,110))

    def test_polymorph_carries_fresh_wallet_and_return_does_not_restore_stale_money(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''entities[100].c.WalletComponent.money=60
        entities[500]={alive=true,x=0,y=0,tags={},c={}}
        ctx.my_player.entity=500;income.on_local_player_polymorphed()
        assert(entities[500].c.WalletComponent.money==60)
        entities[500].c.WalletComponent.money=85
        entities[501]={alive=true,x=0,y=0,tags={},c={WalletComponent={money=100}}}
        ctx.my_player.entity=501;income.on_local_player_polymorphed()
        assert(entities[501].c.WalletComponent.money==85)
        ''')

    def test_dead_host_earns_from_living_client(self):
        p=IncomePeers('''settings.statue=false
        ctx.players.A.entity=200;ctx.players.B.entity=100;ctx.my_player=ctx.players[PEER]
        flags.ew_flag_notplayer_active=PEER=='A'
        ''')
        p.pickup('B');self.assertEqual((p.money('A'),p.money('B')),(110,110))

    def test_existing_run_stays_off_and_bookkeeping_survives(self):
        p=IncomePeers("globals.ew_player_rng='37';globals.unrelated='preserved'")
        self.assertFalse(p.a.eval('ctx.cap.shared_income.enabled()'))
        self.assertEqual(p.a.eval('globals.unrelated'),'preserved')
        self.assertEqual(p.money('A'),100)

    def test_pooled_mode_is_excluded_with_explanation(self):
        p=IncomePeers('ctx.proxy_opt.share_gold=true')
        self.assertFalse(p.a.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('Turn off',p.ledger('.reason'))

    def test_native_callback_substitute_pays_once_and_retires_both_copies(self):
        p=IncomePeers();p.pickup()
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        for lua in p.peers.values():self.assertFalse(lua.eval('entities[gold].alive'))
        p.a.execute("receive('A','claim',encode({decode(globals.ew_income_run_v1).run,'42',0,1}))")
        p.flush();p.tick(60)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        self.assertEqual(p.ledger('.accounts.B.acked'),1)

    def test_both_roles_and_personal_spending(self):
        p=IncomePeers("if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end")
        p.pickup('B');p.a.execute('entities[100].c.WalletComponent.money=70')
        p.pickup('A','43',25)
        self.assertEqual((p.money('A'),p.money('B')),(95,135))

    def test_dead_income_survives_serialized_wallet_and_actual_recovery_module(self):
        p=IncomePeers();p.pickup();p.tick(330)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertEqual(p.money('B'),110)
        self.assertEqual(p.b.eval('entities[300].belongings.wands[2]'),'wand B')
        self.assertAlmostEqual(p.b.eval('entities[300].c.DamageModelComponent.hp'),2.4)

    def test_distinct_coincident_pickups_and_delayed_registration(self):
        p=IncomePeers()
        p.a.execute("gold=nugget('501',10);cross.ew_income_pickup(gold,100)");p.flush()
        self.assertEqual(p.money('A'),100)
        p.a.execute('frame=60;income.on_world_update()');p.flush();p.tick(35)
        p.pickup('A','502',10)
        self.assertEqual((p.money('A'),p.money('B')),(120,120))

    def test_loaded_later_copy_is_retired(self):
        p=IncomePeers();p.pickup()
        p.b.execute("late=nugget('42',10);income.on_world_update();assert(not entities[late].alive)")

    def test_offline_recipient_frozen_and_delivery_replayed_once(self):
        p=IncomePeers()
        p.a.execute("gold=nugget('80',50);frame=60;income.on_world_update();cross.ew_income_pickup(gold,100);ctx.players.B.dc=true")
        p.flush();p.tick(35)
        self.assertEqual(p.money('B'),100)
        self.assertEqual(p.ledger('.accounts.B.next'),1)
        p.a.execute('ctx.players.B.dc=false');p.tick(35)
        self.assertEqual(p.money('B'),150)
        p.a.execute("local s=decode(globals.ew_income_run_v1);receive('A','pay',encode({'B',s.run,{seq=1,id='80',amount=50,collector='A'},decode(globals.ew_income_owner_v1).session}))")
        p.flush();self.assertEqual(p.money('B'),150)

    def test_new_arrival_not_retroactive_and_disconnect_at_claim_not_eligible(self):
        p=IncomePeers('ctx.players.B.dc=true');p.pickup()
        self.assertIsNone(p.ledger('.accounts.B'))
        p.a.execute('ctx.players.B.dc=false');p.tick(35)
        self.assertEqual(p.money('B'),100)

    def test_credit_sequence_and_large_modifier_exactness(self):
        p=IncomePeers()
        p.a.execute('''local l=dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua')
        assert(l.amount(200000,3)==1600000);assert(l.amount(2147483647,20)==2251799812636672)
        assert(l.amount(2147483647,30)==nil)
        assert(l.credit(100,0,{seq=2,amount=10})==nil)
        assert(l.credit(l.MAX,0,{seq=1,amount=1})==nil)
        assert(l.credit(60,0,{seq=1,amount=25})==85)
        ''')

    def test_continue_receipts_and_new_run_reset(self):
        p=IncomePeers();p.pickup()
        for lua in p.peers.values():
            lua.execute('''local before=globals.ew_income_run_v1
            income.on_local_player_spawn();assert(entities[ctx.my_player.entity].c.WalletComponent.money==110)
            assert(decode(globals.ew_income_owner_v1).applied==1)
            ''')
        p.flush();p.tick(35);self.assertEqual(p.money('B'),110)
        fresh=IncomePeers();self.assertEqual(fresh.ledger('.accounts.A.next'),0)
        self.assertEqual(fresh.money('A'),100)

    def test_spectator_suppression_restores_exact_states_and_item_protections(self):
        p=IncomePeers('settings.statue=false')
        p.b.execute('''local v=dofile_once('mods/quant.ew/files/system/spectate/inventory.lua')
        old=EntityAddComponent2(100,'InventoryGuiComponent',{mActive=true,_enabled=true})
        own=EntityAddComponent2(200,'InventoryGuiComponent',{mActive=false,_enabled=false})
        v.hide({100,200});assert(not old.mActive and not old._enabled and not own._enabled)
        v.hide({200});assert(old.mActive and old._enabled)
        v.release();assert(not own.mActive and not own._enabled)
        ''')

    def test_dead_tab_and_chat_switching_never_create_watched_gui(self):
        p=IncomePeers('settings.statue=false')
        p.tick(2);p.b.execute('InputIsKeyJustDown=function(k)return k==55 end');p.tick(1)
        p.b.execute("assert(ctx.spectating_over_peer_id=='A');InputIsKeyJustDown=function(k)return k==43 end")
        p.tick(2);p.b.execute("assert(not entities[100].c.InventoryGuiComponent)")
        p.b.execute('ctx.is_texting=true;InputIsKeyJustDown=function(k)return k==56 end');p.tick(2)
        self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'A')
        p.b.execute('ctx.is_texting=false');p.tick(1)
        self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'B')

    def test_cold_continue_reconnect_with_outstanding_accepted_payment(self):
        p=IncomePeers()
        p.a.execute("gold=nugget('904',25);frame=60;income.on_world_update();cross.ew_income_pickup(gold,100);ctx.players.B.dc=true")
        p.flush();p.tick(35)
        saved=p.snapshot()
        q=IncomePeers("if PEER=='A'then entities[100].c.WalletComponent.money=125 end",saved)
        q.tick(65)
        self.assertEqual((q.money('A'),q.money('B')),(125,125))
        self.assertEqual(q.ledger('.accounts.B.acked'),1)
        q.tick(65);self.assertEqual(q.money('B'),125)

    def test_reordered_payments_wait_for_missing_sequence_and_duplicates_do_not_animate(self):
        p=IncomePeers('settings.statue=false')
        p.b.execute("receive('A','pay',encode({'B',decode(globals.ew_income_owner_v1).run,{seq=2,id='2',amount=10,collector='A'},decode(globals.ew_income_owner_v1).session}))")
        self.assertEqual(p.money('B'),100)
        p.b.execute("receive('A','pay',encode({'B',decode(globals.ew_income_owner_v1).run,{seq=1,id='1',amount=10,collector='A'},decode(globals.ew_income_owner_v1).session}))")
        p.flush();self.assertEqual(p.money('B'),110)
        p.b.execute("receive('A','pay',encode({'B',decode(globals.ew_income_owner_v1).run,{seq=2,id='2',amount=10,collector='A'},decode(globals.ew_income_owner_v1).session}))")
        p.flush();self.assertEqual(p.money('B'),120)

    def test_registration_order_race_and_connected_dead_without_loaded_body(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute("entities[200].alive=false;local s=decode(globals.ew_income_run_v1);receive('A','claim',encode({s.run,'701',0,123}));receive('B','claim',encode({s.run,'701',0,456}));receive('A','register',encode({s.run,'701',{value=50,hp=0,x=0,y=0}}))")
        p.flush();p.tick(35)
        self.assertEqual(p.ledger('.accounts.A.next'),1)
        self.assertEqual(p.ledger('.accounts.B.next'),1)
        self.assertEqual(p.money('A'),150)

    def test_force_tracking_serializes_original_pickable_flag(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local e=EntityCreateNew('new gold')
        EntityAddComponent2(e,'ItemComponent',{is_pickable=true})
        EntityAddComponent2(e,'VariableStorageComponent',{name='gold_value',value_int=10})
        local old=ewext.track
        ewext.track=function(id)assert(entities[id].c.ItemComponent.is_pickable==true);old(id)end
        cross.ew_income_generated(e,true)
        assert(entities[e].c.ItemComponent.is_pickable==false)
        income.on_world_update();assert(entities[e].c.ItemComponent.is_pickable==true)
        ''')

    def test_mismatched_receipt_save_pauses_without_repaying_history(self):
        p=IncomePeers();p.pickup()
        saved=p.snapshot()
        saved['B'].pop('ew_income_owner_v1');saved['B'].pop('__body')
        q=IncomePeers('',saved)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('no recovery receipt',q.b.eval('calls.message'))
        self.assertEqual(q.money('B'),100)
        q.b.execute("assert(cross.ew_income_pickup(nugget('9999',10),ctx.my_player.entity)==true)")

    def test_pending_collector_effect_survives_same_body_reconnect_but_not_later_life(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local o=decode(globals.ew_income_owner_v1)
        o.claims['601']={greed=0,item={value=50,hp=.16,x=0,y=0},life=o.life or 0}
        globals.ew_income_owner_v1=encode(o)
        ''')
        saved=p.snapshot()
        q=IncomePeers('settings.statue=false',saved);q.tick(35)
        q.a.execute('''local effects=EntityGetWithTag('ew_income_effect');assert(#effects==1)
        local target;for _,v in ipairs(entities[effects[1]].vars)do if v._tags=='ew_income_actor'then target=v.value_int end end
        assert(target==ctx.my_player.entity)
        ''')
        saved['A']=dict(saved['A'])
        # A newer body generation must not receive the old body's healing.
        saved['A']['__body']=p.a.eval("(function()local b=decode('"+saved['A']['__body']+"');for _,v in ipairs(b.vars)do if v.name=='ew_income_receipt_v2'then v.value_float=1 end end;return encode(b)end)()")
        q2=IncomePeers('settings.statue=false',saved)
        q2.tick(35);self.assertEqual(q2.a.eval("#EntityGetWithTag('ew_income_effect')"),0)

    def test_chest_identity_single_replay_event_and_recipients_frozen(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local run=decode(globals.ew_income_run_v1).run
        local e={file='data/entities/items/pickup/chest_random.xml',x=10,y=10,rx=10,ry=10}
        receive('B','chest_request',encode({run,'991',e}))
        ctx.players.B.dc=true
        receive('A','chest_request',encode({run,'991',e}))
        local s=decode(globals.ew_income_run_v1)
        assert(s.chests['991'].owner=='B'and #s.chests['991'].participants==2)
        assert(not ctx.cap.shared_income.chest(0,0,0,0,'mods/unknown.xml','900'))
        ''')

    def test_chest_open_before_ordinary_scan_gets_real_gid_only_in_enabled_mode(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local e=EntityCreateNew('chest');entities[e].filename='data/entities/items/pickup/chest_random.xml'
        cross.ew_income_ensure_chest(e)
        assert(entities[e].vars[1].name=='ew_gid_lid')
        local original=ewext.track;ewext.track=function()error('must not enroll twice')end
        cross.ew_income_ensure_chest(e);ewext.track=original
        local clone=EntityCreateNew('replay');entities[clone].filename='data/entities/items/pickup/chest_random.xml'
        EntityAddTag(clone,'ew_no_enemy_sync');cross.ew_income_ensure_chest(clone);assert(not entities[clone].vars)
        local marked=EntityCreateNew('marked replay');entities[marked].filename='data/entities/items/pickup/chest_random.xml'
        EntityAddComponent2(marked,'VariableStorageComponent',{name='ew_income_chest_source',value_string='42'})
        cross.ew_income_ensure_chest(marked);assert(#entities[marked].vars==1)
        ''')
        q=IncomePeers("settings['quant.ew.shared_income']=false")
        q.a.execute('''local e=EntityCreateNew('chest');entities[e].filename='data/entities/items/pickup/chest_random.xml'
        cross.ew_income_ensure_chest(e);assert(not entities[e].vars)
        ''')

    def test_cosmetic_grouping_hidden_backlog_and_duplicate_payment(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local f=dofile_once('mods/quant.ew/files/system/shared_income/feedback.lua')
        f.add('B',10,'Bob',123);frame=5;f.add('B',25,'Bob',123);frame=10;f.update()
        assert(calls.ui:find('Bob +35',1,true))
        ctx.is_texting=true;f.update();ctx.is_texting=false;f.update();assert(calls.ui=='')
        ctx.spectating_over_peer_id='B';f.add('B',10,'Bob',123);ctx.spectating_over_peer_id='A';f.update();assert(calls.ui=='')
        ''')

    def test_untracked_chest_fallback_preserves_personal_loot_but_owned_replay_is_shared(self):
        p=IncomePeers('settings.statue=false')
        p.a.execute('''local e=EntityCreateNew('untracked chest')
        entities[e].filename='data/entities/items/pickup/chest_random.xml';EntityAddTag(e,'ew_no_enemy_sync')
        assert(cross.ew_income_source(e)=='native')
        EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_chest_source',value_string='42',value_bool=true})
        assert(cross.ew_income_source(e)=='share')
        entities[e].vars[1].value_bool=false;assert(cross.ew_income_source(e)=='suppress')
        ''')

    def test_no_native_payment_double_and_blood_money_non_money_effect_kept(self):
        import os,struct
        path=__import__('pathlib').Path(os.environ.get('NOITA_DATA_WAK',r'D:\SteamLibrary\steamapps\common\Noita\data\data.wak'))
        if not path.exists():self.skipTest('Installed Noita data unavailable')
        data=path.read_bytes();pos=16;source=None
        for _ in range(struct.unpack_from('<I',data,4)[0]):
            off,size,n=struct.unpack_from('<III',data,pos);pos+=12;name=data[pos:pos+n].decode();pos+=n
            if name=='data/scripts/items/gold_pickup.lua':source=data[off:off+size].decode()
        lua=LuaRuntime();lua.execute('''
        vars={{name='gold_value',value_int=50},{name='hp_value',value_float=.16}}
        wallet={money=200};fx,healed=0,0
        function dofile_once()end
        function EntityGetTransform()return 0,0 end
        function EntityGetFirstComponent(e,k)return k=='WalletComponent'and wallet or nil end
        function EntityGetComponent()return vars end
        function ComponentGetValue2(c,k)return c[k]end
        ComponentGetValue=ComponentGetValue2;ComponentGetValueInt=ComponentGetValue2;ComponentGetValueFloat=ComponentGetValue2
        function ComponentSetValue2(c,k,v)c[k]=v end
        function shoot_projectile()fx=fx+1 end
        function GameGetGameEffectCount()return 2 end
        function heal_entity(e,n)healed=healed+n end
        function EntityKill()end
        function EntityHasTag(e,k)return k=='ew_income_effect'end
        ''')
        lua.execute(source);lua.execute((ROOT/'quant.ew/files/system/shared_income/pickup.lua').read_text())
        lua.execute("item_pickup(1,2,'')")
        self.assertEqual(lua.eval('wallet.money'),200)
        self.assertEqual(lua.eval('fx'),1)
        self.assertAlmostEqual(lua.eval('healed'),.08)

if __name__=='__main__':unittest.main()
