"""Read-only receipt-qualified gold cleanup before native OnPlayerSpawned."""
import unittest
from lupa.luajit21 import LuaRuntime
from test_shared_income import IncomePeers,ROOT
from test_income_crash_recovery import ALIVE

class StartupGoldTests(unittest.TestCase):
    def startup(self,spawn=False,body_change='',remove_tag=False):
        p=IncomePeers(ALIVE)
        p.b.execute("""gold=nugget('970',10);cross.ew_income_pickup(gold,ctx.my_player.entity)
        entities[gold].parent=ctx.my_player.entity
        entities[gold].c.ItemComponent.inventory_slot={0,0}
        entities[gold].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=false}
        entities[gold].c.SpriteComponent={_tags={enabled_in_world=true},_enabled=false}
        """)
        data=p.snapshot()['B'];gold=p.b.eval('encode(entities[gold])')
        lua=LuaRuntime(unpack_returned_tuples=True);lua.globals().PEER='B'
        lua.globals().READ=lambda path:(ROOT/path.removeprefix('mods/')).read_text(encoding='utf-8')
        traffic=[];lua.globals().SEND=lambda method,payload:traffic.append((method,payload))
        lua.execute((ROOT/'tests/coop_fixture.lua').read_text())
        lua.execute((ROOT/'tests/income_fixture.lua').read_text())
        for k,v in data.items():
            if not k.startswith('__'):lua.globals().globals[k]=v
        lua.execute(ALIVE)
        lua.globals().SAVED_BODY=data['__body'];lua.globals().SAVED_GOLD=gold
        lua.execute("""entities[ctx.my_player.entity]=decode(SAVED_BODY);ctx.ready=false
        restored_gold=9400;entities[restored_gold]=decode(SAVED_GOLD);entities[restored_gold].parent=ctx.my_player.entity
        original_owner=globals.ew_income_owner_v1;calls.tag_restore={}
        EntitySetComponentsWithTagEnabled=function(e,tag,enabled)
            calls.tag_restore[#calls.tag_restore+1]={e,tag,enabled}
            for _,c in pairs(entities[e].c)do if c._tags and c._tags[tag]then c._enabled=enabled end end
        end
        """)
        if remove_tag:lua.execute("EntityRemoveTag(restored_gold,'ew_income_unpaid')")
        lua.execute(body_change)
        lua.execute("income=dofile_once('mods/quant.ew/files/system/shared_income/shared_income.lua')")
        if spawn:lua.execute('income.on_local_player_spawn()')
        return lua,traffic

    def assert_clean(self,lua):
        lua.execute("""assert(EntityGetParent(restored_gold)==0 and entities[restored_gold].alive)
        assert(entities[restored_gold].c.ItemComponent.inventory_slot[1]==-1)
        assert(not entities[restored_gold].c.ItemComponent.is_pickable)
        assert(entities[restored_gold].c.GoldPhysicsComponent._enabled and entities[restored_gold].c.SpriteComponent._enabled)
        assert(entities[ctx.my_player.entity].c.WalletComponent.money==100)
        assert(globals.ew_income_owner_v1==original_owner)
        """)

    def reattach(self,lua):
        lua.execute("""entities[restored_gold].parent=ctx.my_player.entity
        entities[restored_gold].c.ItemComponent.inventory_slot={0,0}
        entities[restored_gold].c.GoldPhysicsComponent._enabled=false
        entities[restored_gold].c.SpriteComponent._enabled=false
        """)

    def test_pre_spawn_nil_owner_and_mode_clean_exact_pending_gold_before_ready(self):
        lua,traffic=self.startup(remove_tag=True)
        lua.execute('income.on_world_update()')
        self.assert_clean(lua);self.assertEqual(traffic,[])
        self.reattach(lua);lua.execute('income.on_world_update_post()')
        self.assert_clean(lua);self.assertEqual(traffic,[])

    def test_on_spawn_and_pre_ready_post_use_same_receipt_proof_without_money(self):
        lua,traffic=self.startup(spawn=True)
        self.assert_clean(lua)
        self.reattach(lua);lua.execute('income.on_world_update()');self.assert_clean(lua)
        self.reattach(lua);lua.execute('income.on_world_update_post()');self.assert_clean(lua)
        self.assertEqual(traffic,[])

    def test_foreign_run_foreign_peer_and_unassigned_identity_do_not_mutate(self):
        mutations=(
            "for _,v in ipairs(entities[ctx.my_player.entity].vars)do if v.name=='ew_income_receipt_v2' then v.value_string='different-run' end end",
            "for _,v in ipairs(entities[ctx.my_player.entity].vars)do if v.name=='ew_income_receipt_owner_v2' then v.value_string='A' end end",
            "ctx.my_id=nil;for _,v in ipairs(entities[ctx.my_player.entity].vars)do if v.name=='ew_income_receipt_owner_v2' then v.value_string=nil end end",
        )
        for change in mutations:
            with self.subTest(change=change):
                lua,traffic=self.startup(body_change=change)
                lua.execute('income.on_world_update();income.on_world_update_post()')
                lua.execute("""assert(EntityGetParent(restored_gold)==ctx.my_player.entity)
                assert(entities[restored_gold].c.ItemComponent.inventory_slot[1]==0)
                assert(not entities[restored_gold].c.GoldPhysicsComponent._enabled)
                assert(#calls.tag_restore==0 and globals.ew_income_owner_v1==original_owner)
                """)
                self.assertEqual(traffic,[])

    def test_foreign_parent_unknown_item_and_unproven_gold_unchanged(self):
        change="""entities[restored_gold].parent=ctx.players.A.entity
        EntityRemoveTag(restored_gold,'ew_income_unpaid')
        unknown=EntityCreateNew('unknown');entities[unknown].parent=ctx.my_player.entity
        EntityAddComponent2(unknown,'ItemComponent',{is_pickable=true,inventory_slot={2,0}})
        unproven=nugget('971',10);entities[unproven].parent=ctx.my_player.entity
        entities[unproven].c.GoldPhysicsComponent={_tags={enabled_in_world=true},_enabled=false}
        """
        lua,traffic=self.startup(body_change=change)
        lua.execute('income.on_world_update();income.on_world_update_post()')
        lua.execute("""assert(EntityGetParent(restored_gold)==ctx.players.A.entity)
        assert(not EntityHasTag(restored_gold,'ew_income_unpaid'))
        assert(EntityGetParent(unknown)==ctx.my_player.entity and entities[unknown].c.ItemComponent.is_pickable)
        assert(EntityGetParent(unproven)==ctx.my_player.entity and not entities[unproven].c.GoldPhysicsComponent._enabled)
        assert(#calls.tag_restore==0 and globals.ew_income_owner_v1==original_owner)
        """)
        self.assertEqual(traffic,[])

    def test_known_income_off_never_applies_early_receipt_cleanup(self):
        lua,traffic=self.startup(spawn=True)
        self.reattach(lua)
        lua.execute("""local o=decode(globals.ew_income_owner_v1)
        receive('A','status',encode({o.run,false,nil,{}, {},{},2}))
        owner_after_status=globals.ew_income_owner_v1;calls.tag_restore={}
        income.on_world_update();income.on_world_update_post()
        assert(EntityGetParent(restored_gold)==ctx.my_player.entity)
        assert(not entities[restored_gold].c.GoldPhysicsComponent._enabled)
        assert(#calls.tag_restore==0 and globals.ew_income_owner_v1==owner_after_status)
        """)
        self.assertEqual(traffic,[])

    def test_partial_startup_globals_retry_without_poisoning_accounting(self):
        lua,traffic=self.startup()
        lua.execute("""local valid=globals.ew_income_owner_v1
        globals.ew_income_owner_v1='not-an-income-receipt'
        income.on_world_update();assert(EntityGetParent(restored_gold)==ctx.my_player.entity)
        globals.ew_income_owner_v1=valid;income.on_world_update()
        """)
        self.assert_clean(lua)
        lua.execute('income.on_local_player_spawn()')
        self.assert_clean(lua);self.assertEqual(traffic,[])


if __name__=='__main__':unittest.main()
