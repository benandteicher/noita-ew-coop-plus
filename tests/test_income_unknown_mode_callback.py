"""Native callback disposition during host-mode initialization; no fake credits."""
import unittest
from lupa.luajit21 import LuaRuntime
from test_shared_income import IncomePeers,ROOT
from test_income_crash_recovery import ALIVE

class UnknownModeCallbackTests(unittest.TestCase):
    def client(self):
        lua=LuaRuntime(unpack_returned_tuples=True);lua.globals().PEER='B'
        lua.globals().READ=lambda path:(ROOT/path.removeprefix('mods/')).read_text(encoding='utf-8')
        traffic=[];lua.globals().SEND=lambda method,payload:traffic.append((method,payload))
        lua.execute((ROOT/'tests/coop_fixture.lua').read_text())
        lua.execute((ROOT/'tests/income_fixture.lua').read_text())
        lua.execute(ALIVE+""";ctx.ready=false
        EntitySetComponentsWithTagEnabled=function(e,tag,enabled)
            for _,c in pairs(entities[e].c)do if c._tags and c._tags[tag]then c._enabled=enabled end end
        end
        income=dofile_once('mods/quant.ew/files/system/shared_income/shared_income.lua')
        gold=nugget('980',10)
        entities[gold].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=true}
        entities[gold].c.SpriteComponent={_tags={enabled_in_world=true},_enabled=true}
        """)
        return lua,traffic

    def attach(self,lua,parent='ctx.my_player.entity'):
        lua.execute("""entities[gold].parent="""+parent+"""
        entities[gold].c.ItemComponent.inventory_slot={0,0}
        entities[gold].c.GoldPhysicsComponent._enabled=false
        entities[gold].c.SpriteComponent._enabled=false
        """)

    def clean(self,lua,pickable=False):
        lua.execute("""assert(entities[gold].alive and EntityGetParent(gold)==0)
        assert(entities[gold].c.ItemComponent.inventory_slot[1]==-1)
        assert(entities[gold].c.GoldPhysicsComponent._enabled and entities[gold].c.SpriteComponent._enabled)
        assert(entities[ctx.my_player.entity].c.WalletComponent.money==100)
        """)
        self.assertEqual(lua.eval('entities[gold].c.ItemComponent.is_pickable'),pickable)

    def off(self,lua):lua.execute("receive('A','status',encode({'run',false,nil,{},{},{},2}));after_status=globals.ew_income_owner_v1")

    def test_unclaimed_shared_callback_late_attach_cleans_without_receipt_or_wallet_write(self):
        lua,traffic=self.client()
        self.assertTrue(lua.eval('cross.ew_income_pickup(gold,ctx.my_player.entity)'))
        self.attach(lua);lua.execute('income.on_world_update_post()');self.clean(lua)
        self.attach(lua);lua.execute('frame=1;income.on_world_update()');self.clean(lua)
        self.assertIsNone(lua.eval('globals.ew_income_owner_v1'));self.assertEqual(traffic,[])

    def test_off_transition_before_and_after_late_attach_releases_world_item(self):
        for status_first in (True,False):
            with self.subTest(status_first=status_first):
                lua,traffic=self.client();lua.execute('assert(cross.ew_income_pickup(gold,ctx.my_player.entity))')
                if status_first:self.off(lua)
                self.attach(lua)
                if not status_first:self.off(lua)
                lua.execute('income.on_world_update_post()');self.clean(lua,True)
                lua.execute("""assert(not EntityHasTag(gold,'ew_income_unpaid'))
                assert(globals.ew_income_owner_v1==after_status)
                assert(not next(decode(after_status).claims)and decode(after_status).applied==0)
                """)
                self.assertEqual(traffic,[])

    def test_ordinary_ewgid_native_gold_falls_through_without_unknown_or_off_mutation(self):
        lua,traffic=self.client();lua.execute("EntityRemoveTag(gold,'ew_income_gold')")
        self.attach(lua)
        lua.execute("""assert(cross.ew_income_pickup(gold,ctx.my_player.entity)==false)
        income.on_world_update_post()
        assert(EntityGetParent(gold)==ctx.my_player.entity and entities[gold].c.ItemComponent.inventory_slot[1]==0)
        assert(not entities[gold].c.GoldPhysicsComponent._enabled and entities[gold].c.ItemComponent.is_pickable)
        """)
        self.off(lua)
        lua.execute("""income.on_world_update();income.on_world_update_post()
        assert(EntityGetParent(gold)==ctx.my_player.entity and entities[gold].c.ItemComponent.inventory_slot[1]==0)
        assert(not entities[gold].c.GoldPhysicsComponent._enabled and entities[gold].c.ItemComponent.is_pickable)
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity)==false)
        assert(globals.ew_income_owner_v1==after_status)
        """)
        self.assertEqual(traffic,[])

    def test_stale_gid_actor_and_expired_callback_proof_do_not_mutate(self):
        for mutation in (
            "for _,v in ipairs(entities[gold].vars)do if v.name=='ew_gid_lid'then v.value_string='981' end end",
            'ctx.my_player.entity=100',
            'frame=3',
            "EntityRemoveTag(gold,'gold_nugget')",
        ):
            with self.subTest(mutation=mutation):
                lua,traffic=self.client();lua.execute('assert(cross.ew_income_pickup(gold,ctx.my_player.entity))')
                self.attach(lua);lua.execute(mutation)
                lua.execute("""income.on_world_update_post()
                assert(EntityGetParent(gold)==200 and entities[gold].c.ItemComponent.inventory_slot[1]==0)
                assert(not entities[gold].c.GoldPhysicsComponent._enabled)
                assert(globals.ew_income_owner_v1==nil)
                """)
                self.assertEqual(traffic,[])

    def test_foreign_parent_never_receives_off_release_or_component_writes(self):
        lua,traffic=self.client();lua.execute('assert(cross.ew_income_pickup(gold,ctx.my_player.entity))')
        self.attach(lua,'ctx.players.A.entity');self.off(lua)
        lua.execute("""local before=entities[gold].c.ItemComponent.is_pickable
        ctx.ready=true;income.on_world_update();income.on_world_update_post()
        frame=3;income.on_world_update();income.on_world_update_post()
        assert(EntityGetParent(gold)==ctx.players.A.entity and entities[gold].c.ItemComponent.inventory_slot[1]==0)
        assert(not entities[gold].c.GoldPhysicsComponent._enabled)
        assert(entities[gold].c.ItemComponent.is_pickable==before)
        assert(EntityHasTag(gold,'ew_income_unpaid'))
        assert(globals.ew_income_owner_v1==after_status)
        """)
        self.assertEqual(traffic,[])

    def test_known_off_world_update_detaches_own_tagged_item_after_local_window(self):
        lua,traffic=self.client();lua.execute('assert(cross.ew_income_pickup(gold,ctx.my_player.entity))')
        self.attach(lua);self.off(lua)
        lua.execute('frame=3;ctx.ready=true;income.on_world_update();income.on_world_update_post()')
        self.clean(lua,True)
        lua.execute("assert(not EntityHasTag(gold,'ew_income_unpaid')and globals.ew_income_owner_v1==after_status)")
        self.assertEqual(traffic,[])

    def test_foreign_actor_enrolled_gold_is_suppressed_without_callback_proof(self):
        lua,traffic=self.client()
        lua.execute("""assert(cross.ew_income_pickup(gold,ctx.players.A.entity)==true)
        entities[gold].parent=ctx.players.A.entity
        entities[gold].c.GoldPhysicsComponent._enabled=false
        income.on_world_update_post()
        assert(EntityGetParent(gold)==ctx.players.A.entity and not entities[gold].c.GoldPhysicsComponent._enabled)
        assert(not EntityHasTag(gold,'ew_income_unpaid')and globals.ew_income_owner_v1==nil)
        """)
        self.assertEqual(traffic,[])

    def test_normal_on_handshake_retries_one_payment_without_phantom_initial_claim(self):
        config=ALIVE+""";local original=load_modules
        function load_modules()
            original()
            if PEER=='B'then
                gold=nugget('982',10)
                assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
                local raw=globals.ew_income_owner_v1
                if raw then local o=decode(raw);assert(not o.claims['982']and o.applied==0)end
                entities[gold].parent=ctx.my_player.entity
                entities[gold].c.ItemComponent.inventory_slot={0,0}
                income.on_world_update_post()
                assert(EntityGetParent(gold)==0 and not entities[gold].c.ItemComponent.is_pickable)
            end
        end
        """
        p=IncomePeers(config);p.tick(4)
        self.assertEqual((p.money('A'),p.money('B')),(100,100))
        p.b.execute("""assert(ctx.cap.shared_income.enabled())
        assert(entities[gold].c.ItemComponent.is_pickable)
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity))
        """)
        p.flush();p.tick(35)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        self.assertEqual(p.ledger('.accounts.B.next'),1)
        self.assertEqual(p.ledger('.accounts.B.acked'),1)


if __name__=='__main__':unittest.main()
