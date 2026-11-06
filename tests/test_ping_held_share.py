"""Read-only held sharing with actual capture and existing root-position helper."""
import unittest
from test_ping_model import runtime


def fixture():
    lua = runtime()
    lua.execute('''
    net={new_rpc_namespace=function()return{opts_reliable=function()end,opts_everywhere=function()end}end}
    ctx={players={}}
    entities={}
    function EntityGetIsAlive(e)return entities[e]~=nil and entities[e].alive~=false end
    function EntityHasTag(e,tag)return entities[e] and entities[e].tags and entities[e].tags[tag] or false end
    function EntityGetRootEntity(e)
      while entities[e] and entities[e].parent do e=entities[e].parent end
      return entities[e] and e or 0
    end
    function EntityGetTransform(e)return entities[e].x,entities[e].y end
    function EntityGetFirstComponentIncludingDisabled(e,name)return entities[e].c[name]end
    function ComponentGetValue2(c,key)return c[key]end
    function ComponentObjectGetValue2(c,object,key)return c[object][key]end
    function EntityGetAllChildren(e)return entities[e].children end
    function mutation()error('inspection must not mutate entities')end
    EntityLoad,EntityKill,EntityAddComponent2,EntityAddChild=mutation,mutation,mutation,mutation
    ComponentSetValue2,ComponentObjectSetValue2,EntitySetTransform=mutation,mutation,mutation
    function wand(e,parent)
      entities[e]={parent=parent,c={AbilityComponent={use_gun_script=true,mana_max=100,mana=83.25,
        mana_charge_speed=20,sprite_file='data/items_gfx/handgun.png',
        gun_config={shuffle_deck_when_empty=false,actions_per_round=1,reload_time=30,deck_capacity=4},
        gunaction_config={fire_rate_wait=12,spread_degrees=1.25,speed_multiplier=.982264}},
        ItemComponent={item_name='Held',always_use_item_name_in_ui=true}}}
    end
    entities[100]={x=10,y=20,c={Inventory2Component={mActiveItem=50}}}
    entities[200]={x=30,y=40,c={}}
    wand(50,100)
    entities[51]={parent=50,c={ItemActionComponent={action_id='LIGHT_BULLET'},
      ItemComponent={uses_remaining=-1,inventory_slot=0,permanently_attached=false}}}
    entities[52]={parent=50,c={ItemActionComponent={action_id='BOMB'},
      ItemComponent={uses_remaining=3,inventory_slot=2,permanently_attached=false}}}
    entities[53]={parent=50,c={ItemActionComponent={action_id='SPEED'},
      ItemComponent={uses_remaining=-1,inventory_slot=0,permanently_attached=true}}}
    entities[50].children={51,52,53}
    player={entity=100,status={is_alive=true}}
    held=dofile_once('mods/quant.ew/files/system/player_ping/held_share.lua')
    ''')
    return lua


class HeldShareTests(unittest.TestCase):
    def test_actual_snapshot_precision_holes_uses_and_no_mutation(self):
        fixture().execute('''
        local data,body,entity=held.capture(player)
        assert(body==100 and entity==50 and data.props.speedMultiplier==.982264)
        assert(data.props.mana==83.25 and data.props.spread==1.25 and data.props.capacity==3)
        assert(data.spells[1]=='LIGHT_BULLET' and data.spells[2]=='' and data.spells[3]=='BOMB')
        assert(data.spell_uses[1]==-1 and data.spell_uses[2]==-1 and data.spell_uses[3]==3)
        assert(data.always_cast_spells[1]=='SPEED' and data.always_cast_uses[1]==-1)
        data.spells[1]='MUTATED';data.props.speedMultiplier=5
        local fresh=held.capture(player)
        assert(fresh.props.speedMultiplier==.982264 and fresh.spells[1]=='LIGHT_BULLET')
        entities[50].c.AbilityComponent.gunaction_config.speed_multiplier=.5
        assert(fresh.props.speedMultiplier==.982264)
        assert(held.capture(player).props.speedMultiplier==.5)
        assert(entities[50].c.AbilityComponent.gun_config.deck_capacity==4)
        assert(entities[52].c.ItemComponent.inventory_slot==2 and entities[52].c.ItemComponent.uses_remaining==3)
        ''')

    def test_explicit_owned_hover_is_not_active_item_and_is_read_only(self):
        fixture().execute('''
        wand(60,100);entities[60].tags={wand=true};entities[60].children={}
        function GameGetAllInventoryItems(body)assert(body==100);return{50,60}end
        local data,body,item=held.capture_owned(player,60)
        assert(data and body==100 and item==60)
        assert(entities[100].c.Inventory2Component.mActiveItem==50)
        assert(entities[60].parent==100 and entities[50].parent==100)
        ''')

    def test_nested_spell_foreign_world_and_absent_enumeration_fail_closed(self):
        for change in ('entities[60].parent=nil','entities[60].parent=200','entities[60].parent=50',
                       'entities[60].tags={card_action=true}', 'entities[60].c.AbilityComponent=nil',
                       'entities[60].alive=false', 'GameGetAllInventoryItems=function()return{50}end'):
            fixture().execute('''
            wand(60,100);entities[60].tags={wand=true};entities[60].children={}
            function GameGetAllInventoryItems(body)
              local out={};for e,v in pairs(entities)do if v.parent==body then out[#out+1]=e end end;return out
            end
            '''+change+';assert(not held.capture_owned(player,60))')

    def test_body_inventory_and_active_wand_are_reacquired_every_call(self):
        fixture().execute('''
        local initial=held.capture(player)
        wand(60,100);entities[60].c.AbilityComponent.gunaction_config.speed_multiplier=.3333
        entities[60].c.AbilityComponent.gun_config.deck_capacity=0
        entities[100].c.Inventory2Component={mActiveItem=60}
        local data,body,entity=held.capture(player)
        assert(body==100 and entity==60 and data.props.speedMultiplier==.3333)
        assert(initial.props.speedMultiplier==.982264)
        entities[101]={x=50,y=60,c={Inventory2Component={mActiveItem=70}}};wand(70,101)
        entities[70].c.AbilityComponent.gun_config.deck_capacity=0
        player.entity=101
        data,body,entity=held.capture(player);assert(body==101 and entity==70)
        local x,y,state=held.anchor(player);assert(x==50 and y==60 and state=='alive')
        ''')

    def test_missing_nonwand_invalid_or_elsewhere_owned_item_is_rejected(self):
        for change in (
            'entities[100].c.Inventory2Component=nil',
            'entities[100].c.Inventory2Component.mActiveItem=0',
            'entities[100].c.Inventory2Component.mActiveItem=nil',
            'entities[100].c.Inventory2Component.mActiveItem=999',
            'entities[50].alive=false',
            'entities[50].parent=200',
            'entities[50].parent=nil',
            'entities[50].c.AbilityComponent=nil',
            'entities[50].c.AbilityComponent.use_gun_script=false',
            'entities[50].c.AbilityComponent.gunaction_config.speed_multiplier=0/0',
        ):
            with self.subTest(change=change):
                fixture().execute(change+';assert(not held.capture(player))')

    def test_anchor_requires_connected_known_living_body(self):
        fixture().execute('''
        local x,y,state=held.anchor(player);assert(x==10 and y==20 and state=='alive')
        local _,_,gone=held.anchor(nil);assert(gone=='gone')
        player.dc=true;_,_,gone=held.anchor(player);assert(gone=='gone' and not held.capture(player))
        player.dc=nil;player.status=nil;_,_,state=held.anchor(player);assert(state=='unknown')
        player.status={};_,_,state=held.anchor(player);assert(state=='unknown')
        player.status={is_alive=false};_,_,state=held.anchor(player);assert(state=='dead')
        player.status.is_alive=true;player.entity=999;_,_,state=held.anchor(player);assert(state=='unknown')
        player.entity=100;entities[100].alive=false;_,_,state=held.anchor(player);assert(state=='unknown')
        entities[100].alive=true;entities[100].x=0/0;_,_,state=held.anchor(player);assert(state=='unknown')
        ''')

    def test_dead_statue_and_ghost_cannot_masquerade_as_living_sender(self):
        for tag in ('heart_statue','ew_notplayer'):
            with self.subTest(tag=tag):
                fixture().execute("entities[100].tags={"+tag+"=true};local x,y,state=held.anchor(player);assert(x==nil and y==nil and state=='dead' and not held.capture(player))")
        fixture().execute('''
        entities[100].tags={polymorphed=true}
        local x,y,state=held.anchor(player);assert(state=='alive' and held.capture(player))
        ''')

    def test_position_and_ownership_follow_current_living_root(self):
        fixture().execute('''
        entities[300]={x=70,y=80,c={}};entities[100].parent=300
        local x,y,state=held.anchor(player);assert(x==70 and y==80 and state=='alive')
        local data,body,entity=held.capture(player);assert(data and body==100 and entity==50)
        entities[300].x=90;assert(held.anchor(player)==90)
        entities[300].tags={heart_statue=true}
        local _,_,state=held.anchor(player);assert(state=='dead' and not held.capture(player))
        ''')

    def test_disappearance_during_reads_fails_without_old_coordinate_fallback(self):
        fixture().execute('''
        assert(held.capture(player));local get=EntityGetFirstComponentIncludingDisabled
        EntityGetFirstComponentIncludingDisabled=function(e,name)
          if e==100 and name=='Inventory2Component'then error('streamed away')end
          return get(e,name)
        end
        assert(not held.capture(player))
        EntityGetRootEntity=function()error('replaced')end
        local x,y,state=held.anchor(player);assert(x==nil and y==nil and state=='unknown')
        ''')


if __name__ == '__main__':
    unittest.main()
