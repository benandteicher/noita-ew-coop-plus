"""Production v2 crash/rejoin protocol under independent Lua peers.

Checkpoints include the native body wallet/receipt and Globals. These are
controlled substitutes; actual Noita termination/checkpoint ordering is tested
separately by the coordinator.
"""
from collections import deque
import copy
import unittest
from lupa.luajit21 import LuaRuntime
from test_shared_income import IncomePeers, ROOT

ALIVE="settings.statue=false;if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end"


def replace_client(p, checkpoint, newer_globals=None):
    """Replace only client game state; host Lua/ledger/session remain running."""
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().PEER='B'
    lua.globals().READ=lambda path:(ROOT/path.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.globals().SEND=lambda method,data:p.q.append(('B',method,data))
    lua.execute((ROOT/'tests/coop_fixture.lua').read_text())
    lua.execute((ROOT/'tests/income_fixture.lua').read_text())
    data=checkpoint['B']
    for k,v in (newer_globals or data).items():
        if not k.startswith('__'):lua.globals().globals[k]=v
    lua.execute(ALIVE)
    lua.globals().RESTORED_BODY=data['__body']
    lua.execute('entities[ctx.my_player.entity]=decode(RESTORED_BODY)')
    lua.globals().flags.ew_flag_notplayer_active=data['__body_dead']
    lua.execute('load_modules()')
    p.b=lua;p.peers['B']=lua
    p.a.execute('income.on_should_send_updates()')
    p.flush();p.tick(65)
    return lua


class CrashRecoveryTests(unittest.TestCase):
    def peers(self):return IncomePeers(ALIVE)

    def test_acknowledged_payment_replayed_from_older_body_even_if_globals_ahead(self):
        p=self.peers();old=p.snapshot();p.pickup('A','50',25)
        self.assertEqual(p.ledger('.accounts.B.acked'),1)
        self.assertIsNone(p.ledger('.accounts.B.pending[1]'))
        self.assertEqual(p.ledger('.accounts.B.history[1].amount'),25)
        current=p.snapshot()
        replace_client(p,old,current['B'])
        self.assertEqual((p.money('A'),p.money('B')),(125,125))
        self.assertTrue(p.b.eval('ctx.cap.shared_income.enabled()'))
        p.tick(120);self.assertEqual(p.money('B'),125)

    def test_body_ahead_of_global_receipt_does_not_duplicate_credit(self):
        p=self.peers();old=p.snapshot();p.pickup('B','51',25);new=p.snapshot()
        replace_client(p,new,old['B'])
        self.assertEqual(p.money('B'),125)
        p.pickup('A','52',10)
        self.assertEqual((p.money('A'),p.money('B')),(135,135))

    def test_before_credit_after_credit_before_ack_and_after_ack(self):
        for boundary in ('before_credit','after_credit_before_ack','after_ack'):
            with self.subTest(boundary=boundary):
                p=self.peers();old=p.snapshot()
                for lua in p.peers.values():lua.execute("gold=nugget('61',10)")
                p.a.execute('frame=60;income.on_world_update()');p.flush()
                p.a.execute('cross.ew_income_pickup(gold,ctx.my_player.entity)');p.flush()
                # One host update enqueues both pay packets, including its
                # synchronous local payment; delivery to B is controlled here.
                p.a.execute('income.on_world_update()')
                packets=list(p.q);p.q.clear()
                for sender,method,data in packets:
                    if method=='pay' and boundary!='before_credit':
                        p.b.globals().receive(sender,method,data)
                if boundary=='after_ack':p.flush()
                # Stale pay/ack from the terminated process can still arrive.
                for packet in packets:p.q.append(packet)
                replace_client(p,old)
                self.assertEqual((p.money('A'),p.money('B')),(110,110))
                self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),1)

    def test_spending_remains_personal_and_replay_is_relative_to_saved_body(self):
        p=self.peers()
        p.b.execute('entities[200].c.WalletComponent.money=65;income.on_world_update_post()')
        old=p.snapshot()
        p.pickup('A','70',25)
        p.a.execute('entities[100].c.WalletComponent.money=80')
        replace_client(p,old)
        self.assertEqual((p.money('A'),p.money('B')),(80,90))

    def test_old_session_pay_and_ack_cannot_skip_or_duplicate_new_body(self):
        p=self.peers();old=p.snapshot()
        oldtoken=p.b.eval('decode(globals.ew_income_owner_v1).session')
        p.pickup('A','71',10);replace_client(p,old)
        newtoken=p.b.eval('decode(globals.ew_income_owner_v1).session')
        self.assertNotEqual(oldtoken,newtoken)
        p.b.globals().OLD_TOKEN=oldtoken;p.a.globals().OLD_TOKEN=oldtoken
        p.b.execute("receive('A','pay',encode({'B',decode(globals.ew_income_owner_v1).run,{seq=2,id='fake',amount=999,collector='A'},OLD_TOKEN}))")
        p.a.execute("receive('B','income_ack',encode({decode(globals.ew_income_run_v1).run,2,OLD_TOKEN}))")
        self.assertEqual(p.money('B'),110)
        self.assertEqual(p.ledger('.accounts.B.acked'),1)
        p.pickup('B','72',10);self.assertEqual(p.money('B'),120)

    def test_host_rollback_and_different_run_fail_actionably_without_wallet_correction(self):
        p=self.peers();old=p.snapshot();p.pickup('A','73',25);current=p.snapshot()
        mixed=copy.deepcopy(current);mixed['A']=old['A']
        q=IncomePeers(ALIVE,mixed);q.tick(60)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('Host journal is older',q.b.eval('calls.message'))
        self.assertEqual(q.money('B'),125)
        changed=copy.deepcopy(current)
        lua=p.b;lua.globals().TEST_BODY=changed['B']['__body']
        changed['B']['__body']=lua.eval("(function()local b=decode(TEST_BODY);for _,v in ipairs(b.vars)do if v.name=='ew_income_receipt_v2' then v.value_string='other-run' end end;return encode(b)end)()")
        q=IncomePeers(ALIVE,changed)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('body/run receipt mismatch',q.b.eval('calls.message'))
        self.assertEqual(q.money('B'),125)

    def test_missing_or_corrupt_paged_history_pauses_without_new_ledger(self):
        p=self.peers();p.pickup('A','74',25);data=p.snapshot()
        broken=copy.deepcopy(data)
        key=next(k for k in broken['A'] if ':history:' in k)
        del broken['A'][key]
        q=IncomePeers(ALIVE,broken)
        self.assertFalse(q.a.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('unreadable recovery journal',q.a.eval('calls.message'))
        self.assertEqual(q.a.eval('globals.ew_income_run_v1'),broken['A']['ew_income_run_v1'])
        self.assertEqual(q.money('A'),125)

    def test_wrong_recipient_body_in_same_run_is_rejected(self):
        p=self.peers();p.pickup('A','800',25);data=p.snapshot()
        data['B']['__body']=data['A']['__body']
        q=IncomePeers(ALIVE,data)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('different peer',q.b.eval('calls.message'))
        self.assertEqual(q.money('B'),125)

    def test_both_games_cold_continue_recovers_only_older_client_body(self):
        p=self.peers();old=p.snapshot();p.pickup('B','801',25)
        data=p.snapshot();data['B']=old['B']
        q=IncomePeers(ALIVE,data);q.tick(65)
        self.assertEqual((q.money('A'),q.money('B')),(125,125))
        for lua in q.peers.values():self.assertTrue(lua.eval('ctx.cap.shared_income.enabled()'))

    def test_dead_checkpoint_replays_earnings_and_revives_with_one_credit(self):
        p=IncomePeers();old=p.snapshot();p.pickup('A','802',25)
        replace_client(p,old)
        self.assertEqual(p.money('B'),125)
        p.tick(330)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertEqual(p.money('B'),125)
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),1)

    def test_new_client_loading_does_not_receive_unenrolled_payments_or_pause(self):
        configure=ALIVE+""";local original=load_modules
        function load_modules()
            original()
            if PEER=='A'then
                gold=nugget('804',10);cross.ew_income_pickup(gold,ctx.my_player.entity)
            end
        end
        """
        p=IncomePeers(configure);p.tick(35)
        self.assertEqual((p.money('A'),p.money('B')),(110,100))
        self.assertTrue(p.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertEqual(p.ledger('.accounts.B.next'),0)
        p.pickup('A','805',10)
        self.assertEqual((p.money('A'),p.money('B')),(120,110))

    def test_startup_gold_gate_is_released_when_host_resolves_income_off(self):
        configure=ALIVE+""";settings['quant.ew.shared_income']=false
        local original=load_modules
        function load_modules()
            original()
            if PEER=='B'then early=nugget('803',10);cross.ew_income_pickup(early,ctx.my_player.entity)
                assert(not entities[early].c.ItemComponent.is_pickable)end
        end
        """
        p=IncomePeers(configure);p.tick(1)
        p.b.execute("assert(entities[early].alive and entities[early].c.ItemComponent.is_pickable)")

    def test_journal_full_reports_actionable_pending_state_to_collector(self):
        p=self.peers();p.a.execute("dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua').HISTORY=2;ctx.players.B.dc=true")
        # Eligibility was frozen while both peers were connected, then prevent
        # delivery of two accepted awards to the still eligible client.
        p.a.execute('ctx.players.B.dc=false')
        for n in (1,2):
            p.a.execute(f"gold=nugget('{810+n}',10);ctx.players.B.dc=false;cross.ew_income_pickup(gold,100);ctx.players.B.dc=true")
            p.flush();p.tick(35)
        p.a.execute("frame=400;ctx.players.B.dc=false;gold=nugget('813',10);cross.ew_income_pickup(gold,100)")
        p.flush()
        self.assertIn('Recovery journal full',p.a.eval('calls.message'))
        self.assertEqual(p.money('A'),120)
        self.assertTrue(p.a.eval('entities[gold].alive'))
        self.assertFalse(p.a.eval('entities[gold].c.ItemComponent.is_pickable'))

    def test_legacy_v1_keeps_strict_pause_and_does_not_invent_deleted_history(self):
        legacy="""local l=dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua')
        local original=l.new;l.new=function(...)local s=original(...);s.version=1;return s end
        """
        p=IncomePeers(ALIVE+';'+legacy);p.pickup('A','820',10);data=p.snapshot()
        self.assertEqual(p.ledger('.version'),1)
        data['B'].pop('ew_income_owner_v1')
        q=IncomePeers(ALIVE,data);q.tick(65)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('Restore matching Noita and proxy saves',q.b.eval('calls.message'))
        self.assertEqual(q.money('B'),110)

    def test_chest_replay_uses_stable_payment_identity_despite_new_physical_gid(self):
        p=self.peers()
        for physical in ('830','831'):
            for lua in p.peers.values():
                lua.execute(f"""source=EntityCreateNew('chest')
                EntityAddComponent2(source,'VariableStorageComponent',{{name='ew_income_chest_source',value_string='900',value_bool=true}})
                logical=cross.ew_income_gold_identity(source);assert(logical=='C:900:1')
                gold=nugget('{physical}',10);cross.ew_income_generated(gold,true,logical)
                """)
            p.a.execute('frame=60;income.on_world_update()');p.flush()
            p.b.execute('cross.ew_income_pickup(gold,ctx.my_player.entity)');p.flush();p.tick(35)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        self.assertEqual(p.ledger('.accounts.B.next'),1)
        self.assertTrue(p.ledger(".claims['C:900:1']"))
        for lua in p.peers.values():self.assertFalse(lua.eval('entities[gold].alive'))

    def test_rain_generator_wrapper_precedes_first_top_level_emission(self):
        p=IncomePeers(ALIVE+""";virtual_files['data/scripts/projectiles/rain_gold.lua']="local entity_id=GetUpdatedEntityID();SetRandomSeed(1,2);local count=1;for i=1,count do rain_gold=EntityLoad('data/entities/items/pickup/goldnugget.xml',0,0) end"
        """)
        p.b.execute("""source=EntityCreateNew('rain')
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_source_v2',value_string='902:1'})
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_owner_v2',value_string='B'})
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='rain'})
        function SetRandomSeed()end
        function GetUpdatedEntityID()return source end
        function CrossCall(name,...)return cross[name](...)end
        EntityLoad=function(path,x,y)local e=EntityCreateNew(path);EntityAddComponent2(e,'ItemComponent',{is_pickable=true});return e end
        """)
        p.b.execute(p.b.eval("virtual_files['data/scripts/projectiles/rain_gold.lua']"))
        p.b.execute("""local identity
        for _,v in ipairs(entities[rain_gold].vars)do if v.name=='ew_income_identity_v2'then identity=v.value_string end end
        assert(identity=='C:902:1:1:1');assert(not entities[rain_gold].c.ItemComponent.is_pickable)
        """)

    def test_deferred_orb_source_counter_and_owner_survive_serialization(self):
        p=self.peers()
        p.b.execute("""source=EntityCreateNew('chest')
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_chest_source',value_string='901',value_bool=true})
        orb=EntityCreateNew('orb');cross.ew_income_money_source(source,orb,'orb')
        assert(cross.ew_income_source(orb)=='share')
        assert(cross.ew_income_gold_identity(orb)=='C:901:1:1')
        saved_orb=encode(entities[orb]);entities[orb]=decode(saved_orb)
        assert(cross.ew_income_gold_identity(orb)=='C:901:1:2')
        """)
        p.a.globals().REMOTE_ORB=p.b.eval('saved_orb')
        p.a.execute("orb=EntityCreateNew('replica');entities[orb]=decode(REMOTE_ORB);assert(cross.ew_income_source(orb)=='suppress')")
        # Native wrapper propagates exactly the known deferred source paths.
        p.b.execute("""function CrossCall(name,...)return cross[name](...)end
        function GetUpdatedEntityID()return source end
        EntityLoad=function(path,x,y)local e=EntityCreateNew(path);EntityAddComponent2(e,'ItemComponent',{is_pickable=true});return e end
        """)
        p.b.execute((ROOT/'quant.ew/files/system/shared_income/generated.lua').read_text())
        p.b.execute("""child=EntityLoad('data/entities/items/pickup/physics_gold_orb.xml',0,0)
        source=child;gold=EntityLoad('data/entities/items/pickup/goldnugget.xml',0,0)
        local identity
        for _,v in ipairs(entities[gold].vars)do if v.name=='ew_income_identity_v2' then identity=v.value_string end end
        assert(identity=='C:901:2:1');assert(not entities[gold].c.ItemComponent.is_pickable)
        """)

    def test_retention_boundary_blocks_expired_body_and_never_evicts_unpaid_or_replaying_credit(self):
        p=self.peers()
        p.a.execute("dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua').HISTORY=2")
        old=p.snapshot()
        p.pickup('A','81',10);p.pickup('A','82',10);p.pickup('A','83',10)
        self.assertEqual(p.ledger('.accounts.B.floor'),1)
        self.assertIsNone(p.ledger('.accounts.B.history[1]'))
        replace_client(p,old)
        self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertIn('outside the 65,536-payment',p.b.eval('calls.message'))
        # Pure journal transactions explicitly cover an old acknowledged high
        # water mark while the current body is replaying from the lower floor.
        p.a.execute("""local l=dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua')
        local s=l.new('bounded',true);local item={value=10,hp=0,x=0,y=0}
        assert(l.claim(s,'1',item,'A',0,{'B'}));assert(l.claim(s,'2',item,'A',0,{'B'}))
        assert(not l.claim(s,'3',item,'A',0,{'B'}));assert(not s.claims['3'])
        l.ack(s,'B',2);s.accounts.B.recovery=0
        assert(not l.claim(s,'3',item,'A',0,{'B'}));assert(s.accounts.B.history[1])
        s.accounts.B.recovery=1
        assert(l.claim(s,'3',item,'A',0,{'B'}));assert(s.accounts.B.floor==1)
        assert(s.accounts.B.history[1]==nil and s.accounts.B.history[2] and s.accounts.B.history[3])
        """)

    def test_unpaid_parented_native_gold_returns_to_world_without_money_or_deletion(self):
        p=self.peers()
        p.b.execute("""gold=nugget('90',10);entities[gold].parent=ctx.my_player.entity
        entities[gold].c.ItemComponent.inventory_slot={3,0}
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
        assert(EntityGetParent(gold)==0 and entities[gold].alive)
        assert(entities[gold].c.ItemComponent.inventory_slot[1]==-1)
        assert(not entities[gold].c.ItemComponent.is_pickable)
        """)
        self.assertEqual(p.money('B'),100)
        p.flush();p.tick(65);self.assertEqual(p.money('B'),110)

    def test_native_attachment_after_callback_is_detached_in_post_without_deletion(self):
        p=self.peers()
        p.b.execute("""gold=nugget('93',10)
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
        -- Model the native engine's observed post-callback parent assignment.
        entities[gold].parent=ctx.my_player.entity
        entities[gold].c.ItemComponent.inventory_slot={0,0}
        income.on_world_update_post()
        assert(EntityGetParent(gold)==0 and entities[gold].alive)
        assert(entities[gold].c.ItemComponent.inventory_slot[1]==-1)
        assert(not entities[gold].c.ItemComponent.is_pickable)
        """)
        self.assertEqual(p.money('B'),100)
        p.flush();p.tick(65);self.assertEqual(p.money('B'),110)

    def test_native_late_pickup_disable_restores_unpaid_world_components_only_for_own_gold(self):
        p=self.peers()
        p.b.execute("""calls.tag_restore={}
        EntitySetComponentsWithTagEnabled=function(e,tag,enabled)
            calls.tag_restore[#calls.tag_restore+1]={e,tag,enabled}
            for _,c in pairs(entities[e].c)do if c._tags and c._tags[tag]then c._enabled=enabled end end
        end
        gold=nugget('950',10)
        entities[gold].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=true}
        entities[gold].c.SpriteComponent={_tags={enabled_in_world=true,enabled_in_inventory=true},_enabled=true}
        entities[gold].c.HandComponent={_tags={enabled_in_hand=true},_enabled=false}
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
        -- Actual native ordering: attach and switch component tags only AFTER
        -- the callback returned. No host approval/credit has happened yet.
        entities[gold].parent=ctx.my_player.entity
        entities[gold].c.ItemComponent.inventory_slot={0,0}
        entities[gold].c.GoldPhysicsComponent._enabled=false
        entities[gold].c.SpriteComponent._enabled=false
        entities[gold].c.HandComponent._enabled=true
        income.on_world_update_post()
        assert(entities[gold].alive and EntityGetParent(gold)==0)
        assert(entities[gold].c.GoldPhysicsComponent._enabled)
        assert(entities[gold].c.SpriteComponent._enabled) -- world wins overlapping tags
        assert(not entities[gold].c.HandComponent._enabled)
        assert(not entities[gold].c.ItemComponent.is_pickable)
        assert(entities[gold].c.ItemComponent.inventory_slot[1]==-1)
        assert(#calls.tag_restore==3)
        -- A saved/chunk-restored root-0 unpaid item can retain the engine's
        -- disabled components. Its existing local claim proves disposition.
        entities[gold].c.GoldPhysicsComponent._enabled=false
        entities[gold].c.SpriteComponent._enabled=false
        income.on_world_update_post()
        assert(entities[gold].c.GoldPhysicsComponent._enabled and entities[gold].c.SpriteComponent._enabled)
        assert(not entities[gold].c.ItemComponent.is_pickable and #calls.tag_restore==6)
        unknown=EntityCreateNew('unknown inventory item');entities[unknown].parent=ctx.my_player.entity
        EntityAddComponent2(unknown,'ItemComponent',{is_pickable=true,inventory_slot={4,0}})
        entities[unknown].c.SpriteComponent={_tags={enabled_in_world=true},_enabled=false}
        assert(cross.ew_income_pickup(unknown,ctx.my_player.entity)==false)
        foreign=nugget('951',10);entities[foreign].parent=ctx.players.A.entity
        entities[foreign].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=false}
        cross.ew_income_pickup(foreign,ctx.players.A.entity)
        foreign_world=nugget('952',10);EntityAddTag(foreign_world,'ew_income_unpaid')
        entities[foreign_world].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=false}
        income.on_world_update_post()
        assert(not entities[unknown].c.SpriteComponent._enabled and EntityGetParent(unknown)==ctx.my_player.entity)
        assert(not entities[foreign].c.GoldPhysicsComponent._enabled and EntityGetParent(foreign)==ctx.players.A.entity)
        assert(not entities[foreign_world].c.GoldPhysicsComponent._enabled)
        assert(#calls.tag_restore==9)
        for _,call in ipairs(calls.tag_restore)do assert(call[1]==gold)end
        """)
        self.assertEqual(p.money('B'),100)
        p.flush();p.tick(65);self.assertEqual(p.money('B'),110)

    def test_paused_known_gold_detaches_but_unknown_item_is_not_mutated(self):
        p=self.peers();data=p.snapshot();data['B'].pop('__body')
        p.pickup('A','91',10)
        newer=p.snapshot();newer['B']=data['B'];q=IncomePeers(ALIVE,newer)
        self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
        q.b.execute("""gold=nugget('92',10);entities[gold].parent=ctx.my_player.entity
        entities[gold].c.ItemComponent.inventory_slot={3,0}
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
        assert(EntityGetParent(gold)==0 and entities[gold].alive)
        assert(not entities[gold].c.ItemComponent.is_pickable)
        unknown=EntityCreateNew('other');entities[unknown].parent=ctx.my_player.entity
        EntityAddComponent2(unknown,'ItemComponent',{is_pickable=true,inventory_slot={4,0}})
        cross.ew_income_pickup(unknown,ctx.my_player.entity)
        assert(EntityGetParent(unknown)==ctx.my_player.entity)
        assert(entities[unknown].c.ItemComponent.inventory_slot[1]==4)
        """)
        self.assertEqual(q.money('B'),100)


if __name__=='__main__':unittest.main()
