"""Read-only ordinary-wand speed service. Native row execution is tested separately."""
from pathlib import Path
import unittest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().READ=lambda path:(ROOT/path.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.execute('''
    actions,spell_lookup,cache={},{},{}
    for i,name in ipairs({'PROJECTILE','STATIC_PROJECTILE','MODIFIER','DRAW_MANY','MATERIAL','OTHER','UTILITY','PASSIVE'})do
      _G['ACTION_TYPE_'..name]=i
    end
    b={installs=0,updates=0,speed_closes=0,capture_closes=0,ready=true,update_ok=true}
    function b.install_speed()b.installs=b.installs+1;b.installed=b.ready;return b.ready end
    function b.update_speeds(data,label_width)
      b.updates=b.updates+1;b.values={}
      b.label_width=label_width
      for _,value in ipairs(data)do b.values[#b.values+1]={id=value.id,text=value.text}end
      return b.update_ok
    end
    function b.close_speed()b.speed_closes=b.speed_closes+1;b.installed=false;return true end
    function b.close()b.capture_closes=b.capture_closes+1;return true end
    function b.speed_status()return{error=b.error or'unsupported renderer'}end
    function dofile_once(path)
      if path:sub(1,5)=='data/'then return end
      if path:find('native_inventory_capture.lua',1,true)then return b end
      if not cache[path]then cache[path]=assert(loadstring(READ(path)))()end
      return cache[path]
    end
    ctx={my_player={entity=100},run_ended=false,is_paused=false,is_wand_pickup=false}
    entities={[100]={c={Inventory2Component={mActiveItem=1}}},[200]={c={Inventory2Component={mActiveItem=4}}}}
    owned={[100]={1,2},[200]={4}};world={2,3}
    function wand(id,speed,script)
      entities[id]={c={AbilityComponent={use_gun_script=script~=false,gunaction_config={speed_multiplier=speed}}}}
    end
    wand(1,.982264);wand(2,2.5);wand(3,.00001);wand(4,.3333333)
    function EntityGetIsAlive(id)return entities[id]~=nil and not entities[id].dead end
    function GameGetAllInventoryItems(body)return owned[body]end
    function EntityGetWithTag(tag)assert(tag=='wand');return world end
    function EntityGetFirstComponentIncludingDisabled(id,name)return entities[id].c[name]end
    function ComponentGetValue2(component,key)return component[key]end
    function ComponentObjectGetValue2(component,object,key)return component[object][key]end
    function GameIsInventoryOpen()return menu==true end
    gui_creates,gui_measures,label_width=0,0,78
    function GuiCreate()gui_creates=gui_creates+1;return 70 end
    function GuiGetTextDimensions(gui,text)
      assert(gui==70 and text=='Projectile speed')
      gui_measures=gui_measures+1;return label_width,8
    end
    printed={};function print(message)printed[#printed+1]=message end
    local function forbidden()error('inspection must not mutate gameplay or inspect enabled feature settings')end
    EntitySetTransform,EntityKill,EntityAddComponent2,EntityLoad,EntityAddChild=forbidden,forbidden,forbidden,forbidden,forbidden
    ComponentSetValue2,ComponentObjectSetValue2,ModSettingGet=forbidden,forbidden,forbidden
    service=dofile_once('mods/quant.ew/files/system/wand_inspection/wand_inspection.lua')
    function by_id(values)local out={};for _,value in ipairs(values)do out[value.id]=value.text end;return out end
    ''')
    return lua


class WandInspectionTests(unittest.TestCase):
    def test_native_layout_receives_measured_label_width_and_reuses_measure_gui(self):
        fixture().execute('''
        service.on_world_update()
        assert(b.label_width==78 and gui_creates==1 and gui_measures==1)
        label_width=111.5;service.on_world_update()
        assert(b.label_width==111.5 and gui_creates==1 and gui_measures==2)
        assert(by_id(b.values)[1]=='0.98x' and by_id(b.values)[2]=='2.50x')
        ''')

    def test_reads_actual_held_nonheld_and_ground_ids_without_equipping_or_mutation(self):
        fixture().execute('''
        world={2,3,1,2};local values=service.inspect_values()
        assert(#values==3 and values[1].id==1 and values[2].id==2 and values[3].id==3)
        local map=by_id(values);assert(map[1]=='0.98x' and map[2]=='2.50x' and map[3]=='1e-05x')
        assert(entities[100].c.Inventory2Component.mActiveItem==1)
        assert(entities[1].c.AbilityComponent.gunaction_config.speed_multiplier==.982264)
        assert(b.installs==0 and b.updates==0) -- enumeration itself never installs a hook
        ''')

    def test_invalid_or_nonwand_values_are_excluded_and_tiny_negative_zero_remain_truthful(self):
        fixture().execute('''
        owned[100]={};world={1,2,3,4,5,6,7,8,9,10}
        wand(1,0/0);wand(2,math.huge);wand(3,-math.huge);wand(4,'1')
        wand(5,1,false);entities[6]={c={}};wand(7,1);entities[7].dead=true
        wand(8,-.00001);wand(9,0);wand(10,1.0107499361038)
        local values=service.inspect_values();local map=by_id(values)
        assert(#values==3 and map[8]=='-1e-05x' and map[9]=='0.00x' and map[10]=='1.01x')
        ''')

    def test_current_body_and_ability_components_are_reacquired(self):
        fixture().execute('''
        service.on_world_update();assert(b.values[1].id==1)
        local old=b.values
        ctx.my_player.entity=200
        entities[4].c.AbilityComponent={use_gun_script=true,gunaction_config={speed_multiplier=.0000025}}
        service.on_world_update()
        assert(b.values[1].id==4 and b.values[1].text=='2.5e-06x')
        assert(old[1].id==1 and old[1].text=='0.98x')
        ctx.my_player=nil;service.on_world_update();assert(b.values[1].id==2 and #b.values==2)
        ''')

    def test_process_local_map_is_limited_to_512_preferring_current_owned_items(self):
        fixture().execute('''
        wand(999,.5);owned[100]={999};world={}
        for id=1,600 do wand(id,1);world[#world+1]=id end
        -- Keep the live body's identity separate from these wand fixtures.
        entities[1000]={c={}};ctx.my_player.entity=1000;owned[1000]={999}
        local values=service.inspect_values();assert(#values==512 and values[1].id==999)
        assert(values[512].id==511)
        ''')

    def test_service_works_with_revival_ping_and_income_disabled(self):
        fixture().execute('''
        ctx.proxy_opt={no_notplayer=false,pvp=true,game_mode='shared_health'}
        ctx.cap={coop_revival={enabled=function()error('must be independent')end}}
        service.on_world_update()
        assert(b.installs==1 and b.updates==1 and b.installed and #b.values==3)
        ''')

    def test_install_failure_closes_and_disables_retries_until_spawn_reset(self):
        fixture().execute('''
        b.ready=false;service.on_world_update()
        assert(b.installs==1 and b.speed_closes==1 and b.updates==0 and #printed==1)
        service.on_world_update();assert(b.installs==1 and #printed==1)
        b.ready=true;service.on_local_player_spawn()
        assert(b.installs==2 and b.updates==1 and b.installed)
        ''')

    def test_update_failure_and_disappearance_fail_back_to_native_then_world_reset_recovers(self):
        fixture().execute('''
        b.update_ok=false;service.on_world_update()
        assert(b.speed_closes==1 and not b.installed)
        service.on_world_update();assert(b.installs==1)
        b.update_ok=true;service.on_world_initialized()
        assert(b.capture_closes==1 and b.speed_closes==2)
        service.on_world_update();assert(b.installs==2 and b.installed)
        ComponentObjectGetValue2=function()error('component disappeared')end
        service.on_world_update();assert(b.speed_closes==3 and not b.installed)
        ''')

    def test_pause_and_run_end_close_owned_row_but_pickup_and_inventory_refresh_both_wands(self):
        fixture().execute('''
        service.on_world_update();local count=b.installs
        ctx.is_paused=true;service.on_inventory_hover_suspend()
        assert(b.speed_closes==1 and b.installs==count)
        ctx.is_wand_pickup=true;service.on_inventory_hover_suspend()
        assert(b.installs==count+1 and by_id(b.values)[2]=='2.50x')
        ctx.is_wand_pickup=false;menu=true;service.on_inventory_hover_suspend()
        assert(b.installs==count+2)
        ctx.run_ended=true;service.on_inventory_hover_suspend()
        assert(b.speed_closes==2)
        service.on_world_update();assert(b.speed_closes==3)
        ''')


if __name__=='__main__':
    unittest.main()
