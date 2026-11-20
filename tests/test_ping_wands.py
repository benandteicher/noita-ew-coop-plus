"""Production Lua snapshot/preview tests; native fonts/layout still need engine QA."""
import pathlib
import hashlib
import unittest
from PIL import Image
from lupa.luajit21 import LuaRuntime

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PingWandTests(unittest.TestCase):
    def runtime(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().READ = lambda p: (ROOT / p.removeprefix('mods/')).read_text(encoding='utf-8')
        lua.execute('''
        cache={}; actions={}; spell_lookup={}; calls={text={},images={},image_widgets={},frames={},widgets={},buttons={}}; entities={}
        for i,k in ipairs({'PROJECTILE','STATIC_PROJECTILE','MODIFIER','DRAW_MANY','MATERIAL','OTHER','UTILITY','PASSIVE'})do _G['ACTION_TYPE_'..k]=i end
        function dofile_once(path)
          if path:sub(1,5)=='data/'then return end
          if cache[path]==nil then cache[path]=assert(loadstring(READ(path)))()end
          return cache[path]
        end
        function EntityGetIsAlive(e)return entities[e]~=nil end
        function EntityGetFirstComponentIncludingDisabled(e,k)return entities[e] and entities[e][k]end
        function EntityGetAllChildren(e)return entities[e].children end
        function ComponentGetValue2(c,k)if k=='inventory_slot'then return c[k],0 elseif k=='offset'then return 2.25,-3.5 end return c[k]end
        function ComponentObjectGetValue2(c,o,k)return c[o][k]end
        local function forbidden()error('engine mutation forbidden')end
        EntityLoad=forbidden;EntityCreateNew=forbidden;EntityAddComponent2=forbidden
        ComponentSetValue2=forbidden;ComponentObjectSetValue2=forbidden;EntityKill=forbidden
        function GuiCreate()return 1 end
        function GuiStartFrame()end
        function GuiIdPushString()end
        function GuiIdPop()end
        function GuiZSetForNextWidget()end
        function GuiColorSetForNextWidget()end
        function GuiOptionsAddForNextWidget()end
        function GuiLayoutBeginHorizontal()end
        function GuiLayoutBeginVertical()end
        function GuiLayoutEnd()end
        function GuiGetTextDimensions(g,t,s)return #t*4*(s or 1),9*(s or 1)end
        function GuiGetImageDimensions(g,path,s)
          local w,h=16,16
          if path:find('/icon_',1,true)then w,h=7,7
          elseif path:find('/projectile_speed.png',1,true)then w,h=7,7
          elseif path:find('/item_bg_',1,true)then w,h=20,20
          elseif path:find('/handgun.png',1,true)then w,h=14,8 end
          return w*(s or 1),h*(s or 1)
        end
        local previous
        local function widget(x,y,w,h,dx,dy,dw,dh)
          previous={x=x,y=y,w=w,h=h,dx=dx or x,dy=dy or y,dw=dw or w,dh=dh or h}
          calls.widgets[#calls.widgets+1]=previous
        end
        function GuiGetPreviousWidgetInfo()
          local b=assert(previous,'widget required');return false,false,false,b.x,b.y,b.w,b.h,b.dx,b.dy,b.dw,b.dh
        end
        function GuiText(g,x,y,t,s)
          calls.text[#calls.text+1]={x=x,y=y,text=t,scale=s or 1}
          local w,h=GuiGetTextDimensions(g,t,s);widget(x,y,w,h)
        end
        function GuiButton(g,id,x,y,t)
          local w,h=GuiGetTextDimensions(g,t);widget(x,y,w,h)
          calls.buttons[#calls.buttons+1]={x=x,y=y,w=w,h=h,text=t}
          local clicked=BUTTON_CLICK==true;BUTTON_CLICK=false;return clicked
        end
        function GuiImage(g,id,x,y,path,a,sx,sy,rot)
          calls.images[#calls.images+1]=path
          local w,h=GuiGetImageDimensions(g,path,sx)
          calls.image_widgets[#calls.image_widgets+1]={path=path,x=x,y=y,w=w,h=h,scale=sx}
          if rot and rot~=0 then widget(x,y,w,h,x,y-w,h,w)else widget(x,y,w,h)end
        end
        function GuiImageNinePiece(g,id,x,y,w,h,alpha)
          local inset=FRAME_INSET or 0
          widget(x,y,w,h,x-inset,y-inset,w+inset*2,h+inset*2)
          calls.frames[#calls.frames+1]={x=x-inset,y=y-inset,w=w+inset*2,h=h+inset*2,alpha=alpha or 1}
        end
        function GameTextGetTranslatedOrNot(t)
          local labels={['$inventory_shuffle']='Shuffle',['$inventory_actionspercast']='Spells/cast',
          ['$inventory_castdelay']='Cast delay',['$inventory_rechargetime']='Recharge time',
          ['$inventory_manamax']='Mana max',['$inventory_manachargespeed']='Mana charge spd.',
          ['$inventory_capacity']='Capacity',['$inventory_spread']='Spread',['$inventory_alwayscasts']='Always casts',
          ['$menu_yes']='Yes',['$menu_no']='No'}
          return labels[t]or t
        end
        GUI_OPTION={Layout_NoLayouting=1}
        S=dofile_once('mods/quant.ew/files/system/player_ping/wand_snapshot.lua')
        E=dofile_once('mods/quant.ew/files/lib/EZWand.lua')
        P=dofile_once('mods/quant.ew/files/system/player_ping/wand_preview.lua')
        function sample(cap)
          local d={v=1,props={shuffle=false,spellsPerCast=1,castDelay=6.25,rechargeTime=-2.75,manaMax=120.5,mana=20.25,manaChargeSpeed=60.5,capacity=cap or 4,spread=-.345,speedMultiplier=.982264},
          spells={},always_cast_spells={'BLACK_HOLE'},spell_uses={},always_cast_uses={2},sprite_image_file='data/items_gfx/handgun.png',offset_x=4,offset_y=3.5,tip_x=12.5,tip_y=2,name='My wand',show_name_in_ui=true}
          for i=1,d.props.capacity do d.spells[i]='';d.spell_uses[i]=-1 end
          if d.props.capacity>0 then d.spells[1]='LIGHT_BULLET';d.spell_uses[1]=0 end
          return d
        end
        function wand_entity(id)
          local d=sample()
          entities[id]={AbilityComponent={use_gun_script=true,mana_max=120.5,mana=20.25,mana_charge_speed=60.5,sprite_file='mods/custom/wand.xml',
          gun_config={shuffle_deck_when_empty=false,actions_per_round=1,reload_time=-2.75,deck_capacity=5},gunaction_config={fire_rate_wait=6.25,spread_degrees=-.345,speed_multiplier=.982264}},
          ItemComponent={item_name='Named wand',always_use_item_name_in_ui=true},SpriteComponent={offset_x=4,offset_y=3.5},HotspotComponent={},children={21,22,23}}
          entities[21]={ItemActionComponent={action_id='BLACK_HOLE'},ItemComponent={inventory_slot=2,uses_remaining=3,permanently_attached=false}}
          entities[22]={ItemActionComponent={action_id='LIGHT_BULLET'},ItemComponent={inventory_slot=0,uses_remaining=-1,permanently_attached=false}}
          entities[23]={ItemActionComponent={action_id='BOMB'},ItemComponent={inventory_slot=0,uses_remaining=2,permanently_attached=true}}
        end
        ''')
        return lua

    def test_capture_precision_slot_holes_always_cast_and_remaining_uses_read_only(self):
        self.runtime().execute('''
        wand_entity(10);local d=assert(S.capture(10))
        assert(d.props.speedMultiplier==.982264 and d.props.spread==-.345 and d.props.castDelay==6.25)
        assert(d.props.capacity==4 and #d.spells==4)
        assert(table.concat(d.spells,',')=='LIGHT_BULLET,,BLACK_HOLE,')
        assert(d.spell_uses[3]==3 and d.spell_uses[1]==-1 and d.always_cast_spells[1]=='BOMB' and d.always_cast_uses[1]==2)
        assert(entities[21].ItemComponent.inventory_slot==2 and entities[22].ItemComponent.inventory_slot==0)
        assert(d.sprite_image_file=='data/items_gfx/handgun.png' and d.tip_x==2.25 and d.tip_y==-3.5)
        entities[21].ItemComponent.inventory_slot=0
        local e=assert(S.capture(10));assert(e.spells[1]=='BLACK_HOLE' and e.spells[2]=='LIGHT_BULLET')
        assert(S.capture(0)==nil and S.capture(999)==nil)
        ''')

    def test_native_capacity_includes_always_casts_but_snapshot_slots_do_not(self):
        self.runtime().execute('''
        wand_entity(10)
        entities[10].AbilityComponent.gun_config.deck_capacity=80
        entities[10].children={}
        for i=1,16 do
          local id=100+i
          entities[id]={ItemActionComponent={action_id='BOMB'},ItemComponent={inventory_slot=0,uses_remaining=i,permanently_attached=true}}
          entities[10].children[i]=id
        end
        local d=assert(S.capture(10));assert(d.props.capacity==64 and #d.spells==64 and #d.always_cast_spells==16)
        assert(d.always_cast_uses[16]==16 and entities[10].AbilityComponent.gun_config.deck_capacity==80)
        entities[10].AbilityComponent.gun_config.deck_capacity=15
        assert(S.capture(10)==nil)
        entities[10].AbilityComponent.gun_config.deck_capacity=81
        assert(S.capture(10)==nil)
        ''')

    def test_strict_payload_bounds_plain_tables_dense_lists_and_safe_art(self):
        self.runtime().execute('''
        local function reject(change)local d=sample();change(d);assert(S.validate(d)==nil)end
        reject(function(d)d.extra={}end)
        reject(function(d)d.props.speedMultiplier=0/0 end)
        reject(function(d)d.props.spread=math.huge end)
        reject(function(d)d.props.capacity=65 end)
        reject(function(d)d.props.capacity=4.5 end)
        reject(function(d)d.spells[2]=nil end)
        reject(function(d)d.spells[1]=string.rep('A',65)end)
        reject(function(d)d.spells[1]='../BAD'end)
        reject(function(d)d.spells=setmetatable(d.spells,{})end)
        reject(function(d)setmetatable(d,{__index=function()error('metatable invoked')end})end)
        reject(function(d)d.spell_uses=false end)
        reject(function(d)d.spell_uses[1]=1.5 end)
        reject(function(d)d.offset_x=100000 end)
        reject(function(d)d.name=string.rep('a',10000)end)
        reject(function(d)d.props.unknown=1 end)
        reject(function(d)d.always_cast_spells[1]=''end)
        local d=sample();d.sprite_image_file='../../sneaky.xml';d.name='hi\\n'..string.rep('é',80)
        local v=assert(S.validate(d));assert(v.sprite_image_file=='data/items_gfx/handgun.png' and #v.name<=96 and not v.name:find('\\n'))
        d.sprite_image_file='data/items_gfx/wands/wand_1000.png';assert(S.validate(d).sprite_image_file==d.sprite_image_file)
        d.sprite_image_file='data/items_gfx/wands/wand_1001.png';assert(S.validate(d).sprite_image_file=='data/items_gfx/handgun.png')
        d.props.mana=123;assert(v.props.mana~=123)
        local big=sample(64);big.always_cast_spells={};big.always_cast_uses={}
        for i=1,16 do big.always_cast_spells[i]=string.format('A%02d',i)..string.rep('A',61);big.always_cast_uses[i]=1000000 end
        for i=1,64 do big.spells[i]=string.format('Z%02d',i)..string.rep('Z',61);big.spell_uses[i]=1000000 end
        local valid=assert(S.validate(big));local bitser=dofile_once('mods/quant.ew/files/lib/bitser.lua')
        local packet=bitser.dumps(valid);assert(#packet<8192)
        local copy=assert(S.validate(bitser.loads(packet)));assert(copy.props.speedMultiplier==.982264 and copy.props.spread==-.345)
        big.always_cast_spells[17]='B';assert(S.validate(big)==nil)
        ''')

    def test_static_renderer_keeps_default_and_shows_speed_uses_and_bounded_overflow(self):
        self.runtime().execute('''
        local d=sample(64);d.always_cast_spells={};d.always_cast_uses={}
        for i=1,16 do d.always_cast_spells[i]='UNKNOWN_SPELL';d.always_cast_uses[i]=2 end
        local plan=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        assert(plan.width<=419 and plan.height<=232 and plan.visible_slots==64)
        local wider=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=204}))
        assert(wider.visible_slots==64 and wider.height<=204 and wider.columns>plan.columns)
        local box=E.RenderTooltip(9,9,d,1,-100,plan);assert(box and box.w==plan.width and box.h==plan.height)
        local speed,uses,unknown=false,false,false
        for _,v in ipairs(calls.text)do speed=speed or v.text=='0.98x';uses=uses or v.text=='0' end
        for _,v in ipairs(calls.images)do unknown=unknown or v=='data/ui_gfx/gun_actions/unidentified.png'end
        assert(speed and uses and unknown)
        assert(E.FormatSpeedMultiplier(.982264)=='0.98x' and E.FormatSpeedMultiplier(.000001)=='1e-06x')
        assert(E.FormatSpeedMultiplier(-.000001)=='-1e-06x' and E.FormatSpeedMultiplier(0)=='0.00x')
        calls.text={};local small=assert(E.MeasureTooltip(1,d,{max_width=240,max_height=180}));assert(small.height<=180 and small.visible_slots<64)
        E.RenderTooltip(9,9,d,1,0,small)
        local more=false;for _,v in ipairs(calls.text)do more=more or v.text:find(' slots',1,true)~=nil end;assert(more)
        assert(E.MeasureTooltip(1,d,{max_width=200,max_height=100})==nil)
        calls.text={};E.RenderTooltip(0,0,sample(),1,0)
        for _,v in ipairs(calls.text)do assert(v.text~='Projectile speed' and v.text~='0.98x')end
        ''')

    def test_hover_delay_sticky_card_expiry_hidden_body_changes_and_edges(self):
        self.runtime().execute('''
        local marker={key='x',sender='A',x=410,y=230,w=8,h=8,record={wand=sample(64)}}
        local o={now=0,w=427,h=240,mx=414,my=234,body=1}
        assert(P.draw(1,{marker},o)==nil);o.now=11;assert(P.draw(1,{marker},o)==nil)
        o.now=12;local b=assert(P.draw(1,{marker},o));assert(b.x>=0 and b.y>=0 and b.x+b.w<=427 and b.y+b.h<=240)
        local heading,captured=false,false;for _,v in ipairs(calls.text)do heading=heading or v.text=='WAND';captured=captured or v.text=='captured'end;assert(heading and captured)
        assert(P.active_key()=='x' and P.last_bounds().actual==b)
        -- The generous bridge from dot to nearest card edge remains interactive.
        local bx=math.max(b.x,math.min(414,b.x+b.w));local by=math.max(b.y,math.min(234,b.y+b.h))
        o.mx=(414+bx)/2;o.my=(234+by)/2;o.now=12.5;assert(P.draw(1,{marker},o))
        o.now=13;o.mx=b.x+20;o.my=b.y+20;local c=assert(P.draw(1,{marker},o));assert(c.x==b.x and c.y==b.y)
        o.now=14;assert(P.draw(1,{},o)==nil)
        assert(P.active_key()==nil and P.last_bounds()==nil)
        o.mx=414;o.my=234;o.now=15;P.draw(1,{marker},o);o.now=27;assert(P.draw(1,{marker},o))
        o.hidden=true;assert(P.draw(1,{marker},o)==nil);o.hidden=false;o.now=28;assert(P.draw(1,{marker},o)==nil)
        o.now=40;assert(P.draw(1,{marker},o));o.body=2;o.now=41;assert(P.draw(1,{marker},o)==nil)
        o.now=53;assert(P.draw(1,{marker},o));o.now=1;assert(P.draw(1,{marker},o)==nil)
        P.clear();marker.x=0;marker.y=0;o.mx=1;o.my=1;o.now=0;P.draw(1,{marker},o);o.now=12
        local corner=assert(P.draw(1,{marker},o));assert(corner.x>=0 and corner.y>=0 and corner.x+corner.w<=427 and corner.y+corner.h<=240)
        o.mx=426;o.my=239;o.now=13;assert(P.draw(1,{marker},o));o.now=29;assert(P.draw(1,{marker},o)==nil)
        ''')

    def test_no_redundant_held_speed_line_when_inventory_is_open(self):
        self.runtime().execute('''
        wand_entity(10);entities[100]={Inventory2Component={mActiveItem=10}}
        assert(P.draw_held(1,100,{w=427,h=240})==nil and #calls.text==0)
        assert(P.draw_held(1,100,{now=0,w=427,h=240})==nil and #calls.text==0)
        ''')

    def test_receiver_receipt_away_never_opens_then_core_dwell_card_entry_and_leave(self):
        self.runtime().execute('''
        local marker={key='receipt',sender='A',x=70,y=70,w=70,h=60,
          core_box={x=100,y=100,w=7,h=7},record={seq=1,source='held',wand=sample(8)}}
        local o={now=0,w=427,h=240,mx=400,my=225,body=1}
        -- Receiving a valid shared snapshot has no auto-open side effect.
        for _,t in ipairs({0,12,90})do
          o.now=t;calls.frames={}
          assert(P.draw(1,{marker},o)==nil and P.active_key()==nil and #calls.frames==0)
        end
        o.mx=103;o.my=103;o.now=100;assert(P.draw(1,{marker},o)==nil)
        o.now=111;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        o.now=112;local card=assert(P.draw(1,{marker},o));assert(P.active_key()=='receipt')
        local cx,cy=103.5,103.5
        local ex=math.max(card.x,math.min(cx,card.x+card.w))
        local ey=math.max(card.y,math.min(cy,card.y+card.h))
        o.mx=(cx+ex)/2;o.my=(cy+ey)/2;o.now=113
        local passage=assert(P.draw(1,{marker},o));assert(passage.x==card.x and passage.y==card.y)
        o.mx=card.x+card.w/2;o.my=card.y+card.h/2;o.now=114
        local inside=assert(P.draw(1,{marker},o));assert(inside.x==card.x and inside.y==card.y)
        o.mx=400;o.my=225;o.now=115;calls.frames={}
        assert(P.draw(1,{marker},o));o.now=131;calls.frames={}
        assert(P.draw(1,{marker},o)==nil and P.active_key()==nil and P.last_bounds()==nil and #calls.frames==0)
        -- Leaving the card does not leave an already-satisfied dwell behind.
        o.mx=103;o.my=103;o.now=132;assert(P.draw(1,{marker},o)==nil)
        o.now=143;assert(P.draw(1,{marker},o)==nil)
        o.now=144;assert(P.draw(1,{marker},o))
        ''')

    def test_receiver_replacement_expiry_menu_and_body_clear_open_card(self):
        self.runtime().execute('''
        local marker={key='original',sender='A',x=100,y=100,w=7,h=7,
          core_box={x=100,y=100,w=7,h=7},record={seq=1,source='held',wand=sample(64)}}
        local o={now=0,w=427,h=240,mx=103,my=103,body=1}
        P.draw(1,{marker},o);o.now=12;local old=assert(P.draw(1,{marker},o))
        -- A fresh receipt key replacing this sender's old record starts a new dwell.
        marker.key='replacement';marker.record.wand=sample(1)
        o.now=13;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        o.now=24;assert(P.draw(1,{marker},o)==nil)
        o.now=25;local replacement=assert(P.draw(1,{marker},o));assert(P.active_key()=='replacement')
        assert(replacement.h<old.h)
        -- Expiry/disconnect filtering belongs to the adapter: the marker disappears.
        o.mx=replacement.x+20;o.my=replacement.y+20;o.now=26
        assert(P.draw(1,{},o)==nil and P.active_key()==nil and P.last_bounds()==nil)
        o.mx=103;o.my=103;o.now=27;P.draw(1,{marker},o)
        o.now=39;assert(P.draw(1,{marker},o));o.hidden=true;o.now=40
        assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        o.hidden=nil;o.now=41;assert(P.draw(1,{marker},o)==nil)
        o.now=53;assert(P.draw(1,{marker},o))
        o.body=2;o.now=54;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        o.now=66;assert(P.draw(1,{marker},o))
        P.clear();o.now=67;assert(P.draw(1,{marker},o)==nil and P.last_bounds()==nil)
        ''')

    def test_deliberate_higher_sequence_refreshes_snapshot_while_card_stays_anchored(self):
        self.runtime().execute('''
        local marker={key='same',sender='A',x=400,y=100,w=8,h=8,record={seq=1,wand=sample(64)}}
        local o={now=0,w=427,h=240,mx=404,my=104,body=1}
        P.draw(1,{marker},o);o.now=12;local original=assert(P.draw(1,{marker},o))
        marker.record.wand.props.speedMultiplier=1.23456;marker.record.seq=2;calls.text={};o.now=13
        local updated=assert(P.draw(1,{marker},o));assert(updated.x==original.x and updated.y==original.y and updated.w==original.w and updated.h==original.h)
        local text='';for _,v in ipairs(calls.text)do text=text..v.text..'|'end
        assert(text:find('1.23x',1,true) and not text:find('0.98x',1,true))
        -- Merely changing a source object without a new sequence does not turn it live.
        marker.record.wand.props.speedMultiplier=8;calls.text={};o.now=14;P.draw(1,{marker},o)
        text='';for _,v in ipairs(calls.text)do text=text..v.text..'|'end
        assert(text:find('1.23x',1,true) and not text:find('8.00x',1,true))
        marker.record.wand=sample(1);marker.record.seq=3;o.now=15
        local resized=assert(P.draw(1,{marker},o));assert(resized.x==original.x and resized.y==original.y and resized.h<original.h)
        assert(resized.x+resized.w<=427 and resized.y+resized.h<=240)
        ''')

    def test_share_acknowledgement_is_brief_contextual_and_has_no_held_substitution(self):
        self.runtime().execute('''
        entities[100]={Inventory2Component={mActiveItem=0}}
        GuiButton=function()error('no permanent sharing button')end
        Snapshot=nil -- drawing confirmation must not inspect/equip a wand
        local o={now=10,w=427,h=240,shared_until=100}
        local function show(t)calls.text={};o.now=t;return P.draw_held(1,100,o)end
        local b=assert(show(10));assert(b.x==8 and b.y==224 and #calls.text==1 and calls.text[1].text=='Shared')
        assert(#calls.buttons==0);P.clear();assert(show(99));assert(not show(100) and #calls.text==0)
        for _,bad in ipairs({0,-1,math.huge,0/0,'100'})do o.shared_until=bad;assert(not show(10))end
        o.shared_until=200;o.hidden=true;assert(not show(11));o.hidden=nil
        assert(P.draw_held(1,999,o)==nil);o.shared_until=nil;assert(not show(12))
        for _,size in ipairs({{427,240},{100,240},{427,24}})do
          o.w,o.h,o.now,o.shared_until=size[1],size[2],10,100
          local box=assert(P.draw_held(1,100,o));assert(box.x>=4 and box.y>=4 and box.x+box.w<=o.w-4 and box.y+box.h<=o.h-4)
        end
        ''')

    def test_shared_card_qualifier_world_default_and_source_change_replans(self):
        self.runtime().execute('''
        local marker={key='shared',sender='A',x=100,y=100,w=7,h=7,record={seq=1,source='held',wand=sample(8)}}
        local o={now=0,w=427,h=240,mx=103,my=103,body=1}
        P.draw(1,{marker},o);o.now=12;calls.text={};assert(P.draw(1,{marker},o))
        local shared,captured=false,false
        for _,v in ipairs(calls.text)do shared=shared or v.text=='shared';captured=captured or v.text=='captured'end
        assert(shared and not captured)
        marker.record.source=nil;o.now=13;calls.text={};assert(P.draw(1,{marker},o))
        shared,captured=false,false
        for _,v in ipairs(calls.text)do shared=shared or v.text=='shared';captured=captured or v.text=='captured'end
        assert(captured and not shared)
        ''')

    def test_native_eight_slot_card_is_content_sized_with_compact_gap_and_measured_speed_icon(self):
        self.runtime().execute('''
        local d=sample(8);local plan=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        assert(plan.width<200 and plan.height<150 and plan.visible_slots==8 and not plan.overflow)
        local spell_rows=math.ceil(8/plan.columns);assert(spell_rows==1)
        assert(plan.value_x-plan.label_x<100)
        assert(plan.rows[9].icon=='mods/quant.ew/files/system/player_ping/assets/projectile_speed.png' and plan.rows[9][2]=='Projectile speed')
        assert(plan.rows[9].iw==7 and plan.rows[9].ih==7)
        calls.text={};calls.widgets={};E.RenderTooltip(9,9,d,1,-100,plan)
        for _,v in ipairs(calls.text)do
          assert(v.text~='x' and v.text~='WAND SNAPSHOT')
          if v.text~='0' and v.text~='2' then assert(v.scale==1)end
        end
        ''')

    def test_unbounded_renderer_body_remains_byte_identical_to_r2(self):
        source=(ROOT/'quant.ew/files/lib/EZWand.lua').read_text(encoding='utf-8')
        start=source.index('    GuiIdPushString(gui, "EZWand_tooltip")')
        end=source.index('\nlocal function refresh_wand_if_in_inventory',start)
        self.assertEqual(hashlib.sha256(source[start:end].encode()).hexdigest(),
                         '6c98ab8778f34c4a94d300810e83aeb5f9eee5df5f37a09873d9076d07852703')

    def test_speed_icon_is_seven_pixel_white_lightning_and_draws_aligned_with_native_icons(self):
        asset=ROOT/'quant.ew/files/system/player_ping/assets/projectile_speed.png'
        with Image.open(asset) as icon:
            self.assertEqual(icon.size,(7,7))
            self.assertEqual(set(icon.convert('RGBA').get_flattened_data()),{(0,0,0,0),(255,255,255,255)})
            self.assertEqual(sum(icon.convert('RGBA').getpixel((x,3))[3]>0 for x in range(7)),5)
        self.runtime().execute('''
        local d=sample(8);local p=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        E.RenderTooltip(9,9,d,1,-100,p)
        local speed
        for _,v in ipairs(calls.image_widgets)do if v.path:find('/projectile_speed.png',1,true)then speed=v end end
        assert(speed and speed.w==7 and speed.h==7 and speed.scale==1)
        assert(speed.x==9 and speed.y==9+p.stat_y+p.rows[9].y+(p.rows[9].h-7)/2)
        ''')

    def test_native_slot_stride_is_separate_from_decorative_extent_for_occupied_empty_and_always_cast(self):
        self.runtime().execute('''
        local d=sample(5);d.spells={'LIGHT_BULLET','','BLACK_HOLE','','BOMB'};d.spell_uses={3,-1,5,-1,2}
        d.always_cast_spells={'BOMB','UNKNOWN'};d.always_cast_uses={3,3}
        local p=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        assert(p.pitch_x==14 and p.pitch_y==14)
        assert(math.abs((p.slot_w+2-.3)-p.pitch_x)<.01)
        assert(p.background_w>p.pitch_x and p.slot_w<p.pitch_x)
        E.RenderTooltip(9,9,d,1,-100,p)
        local boxes,borders,art={},{},{}
        for _,v in ipairs(calls.image_widgets)do
          if v.path=='data/ui_gfx/inventory/inventory_box.png'then boxes[#boxes+1]=v end
          if v.path:find('/item_bg_',1,true)then borders[#borders+1]=v end
          if v.path=='data/ui_gfx/gun_actions/unidentified.png'then art[#art+1]=v end
        end
        assert(#boxes==5 and #borders==5 and #art==5)
        for i=2,5 do assert(math.abs(boxes[i].x-boxes[i-1].x-14)<.00001 and boxes[i].y==boxes[1].y)end
        assert(math.abs(borders[2].x-borders[1].x-14)<.00001)
        assert(math.abs(borders[3].x-boxes[1].x-p.border_x)<.00001)
        assert(math.abs(borders[4].x-boxes[3].x-p.border_x)<.00001)
        assert(math.abs(borders[5].x-boxes[5].x-p.border_x)<.00001)
        assert(math.abs(art[3].x-boxes[1].x-.43479339599609)<.00001)
        assert(math.abs(art[3].y-boxes[1].y-.4)<.00001)
        ''')

    def test_long_uses_do_not_inflate_pitch_and_keep_exact_native_font_notes_bounded(self):
        self.runtime().execute('''
        local d=sample(8);local ordinary=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        d.spell_uses[1]=1000000;d.always_cast_uses[1]=1000000
        local p=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        assert(p.pitch_x==ordinary.pitch_x and p.pitch_y==ordinary.pitch_y and p.slot_w==ordinary.slot_w)
        assert(p.long_counts['slot:1']=='Slot 1: 1000000 uses' and p.long_counts['ac:1']=='Always cast 1: 1000000 uses')
        calls.text={};calls.widgets={};local box=E.RenderTooltip(9,9,d,1,-100,p)
        local slot,ac,plus=false,false,false
        for _,v in ipairs(calls.text)do
          slot=slot or (v.text=='Slot 1: 1000000 uses' and v.scale==1)
          ac=ac or (v.text=='Always cast 1: 1000000 uses' and v.scale==1)
          plus=plus or v.text=='+'
          assert(not v.text:find('1e',1,true))
        end
        assert(slot and ac and plus)
        for _,b in ipairs(calls.widgets)do assert(b.dx>=box.x-.001 and b.dy>=box.y-.001 and b.dx+b.dw<=box.x+box.w+.001 and b.dy+b.dh<=box.y+box.h+.001)end
        local all=sample(64);all.always_cast_spells={};all.always_cast_uses={}
        for i=1,64 do all.spells[i]='BOMB';all.spell_uses[i]=1000000 end
        for i=1,16 do all.always_cast_spells[i]='BOMB';all.always_cast_uses[i]=1000000 end
        local q=assert(E.MeasureTooltip(1,all,{max_width=419,max_height=232}))
        assert(q.pitch_x==14 and q.pitch_y==14 and q.visible_slots==64 and q.note_summary=='+77 count notes: hover +')
        local n=63;q.mx=9+q.grid_left+(n%q.columns)*14+3;q.my=9+q.spells_y+q.grid_top+math.floor(n/q.columns)*14+3
        calls.text={};local actual=E.RenderTooltip(9,9,all,1,-100,q)
        local full=false;for _,v in ipairs(calls.text)do full=full or v.text=='Slot 64: 1000000 uses'end
        assert(full and actual.w<=419 and actual.h<=232)
        ''')

    def test_bottom_inset_covers_all_content_borders_counts_wrap_alwayscasts_and_notes(self):
        self.runtime().execute('''
        local examples={sample(4),sample(24),sample(64),sample(0)}
        examples[2].always_cast_spells={'BOMB','BLACK_HOLE','BOMB'};examples[2].always_cast_uses={3,2,1000000}
        examples[3].always_cast_spells={};examples[3].always_cast_uses={}
        for i=1,64 do examples[3].spells[i]='BOMB';examples[3].spell_uses[i]=1000000 end
        for _,d in ipairs(examples)do
          for _,height in ipairs({232,180})do
            local p=E.MeasureTooltip(1,d,{max_width=419,max_height=height})
            if p then
              calls.widgets={};local box=E.RenderTooltip(9,9,d,1,-100,p)
              local lowest=-math.huge
              for i,b in ipairs(calls.widgets)do
                if i<#calls.widgets and not(b.x==5 and b.y==5 and b.w==20 and b.h==20)then lowest=math.max(lowest,b.dy+b.dh)end
              end
              assert(box.y+box.h-lowest>=5-.001)
              assert(p.pitch_x==14 and p.pitch_y==14 and box.h<=height)
            end
          end
        end
        ''')

    def test_hover_uses_visible_core_not_reserved_layout_and_preserves_sticky_card(self):
        self.runtime().execute('''
        local marker={key='core',sender='A',x=100,y=100,w=70,h=50,core_box={x=130,y=123,w=7,h=7},record={seq=1,wand=sample(8)}}
        local o={now=0,w=427,h=240,mx=105,my=105,body=1}
        P.draw(1,{marker},o);o.now=20;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        o.mx=129;o.my=125;o.now=21;assert(P.draw(1,{marker},o)==nil)
        o.now=33;local actual=assert(P.draw(1,{marker},o));assert(P.active_key()=='core')
        assert(actual.x==marker.core_box.x+marker.core_box.w+8)
        o.now=34;o.mx=actual.x+20;o.my=actual.y+20;assert(P.draw(1,{marker},o))
        o.now=35;o.mx=105;o.my=105;assert(P.draw(1,{marker},o));o.now=51;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
        P.clear();o.mx=marker.core_box.x-3.1;o.my=125;o.now=0;P.draw(1,{marker},o);o.now=20;assert(P.draw(1,{marker},o)==nil)
        ''')

    def test_returned_actual_matches_native_frame_extent_and_encloses_drawn_contents(self):
        self.runtime().execute('''
        for _,cap in ipairs({0,1,8,64})do
          local d=sample(cap)
          if cap==64 then for i=2,16 do d.always_cast_spells[i]='UNKNOWN';d.always_cast_uses[i]=2 end end
          local p=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
          calls.widgets={};local actual=assert(E.RenderTooltip(14,14,d,1,-100,p));local frame=calls.frames[#calls.frames]
          assert(actual.x==frame.x and actual.y==frame.y and actual.w==frame.w and actual.h==frame.h)
          for _,b in ipairs(calls.widgets)do
            assert(b.dx>=actual.x-.001 and b.dy>=actual.y-.001)
            assert(b.dx+b.dw<=actual.x+actual.w+.001 and b.dy+b.dh<=actual.y+actual.h+.001)
          end
        end
        ''')

    def test_preview_reports_actual_and_planned_separately_and_clamps_native_frame_insets_at_corners(self):
        self.runtime().execute('''
        FRAME_INSET=2
        for _,cap in ipairs({8,64})do for _,corner in ipairs({{0,0},{418,0},{0,232},{418,232}})do
          P.clear();local d=sample(cap)
          if cap==64 then for i=2,16 do d.always_cast_spells[i]='UNKNOWN';d.always_cast_uses[i]=2 end end
          local marker={key='corner',sender='A',x=corner[1],y=corner[2],w=8,h=8,record={seq=1,wand=d}}
          local o={now=0,w=427,h=240,mx=marker.x+2,my=marker.y+2,body=1}
          assert(P.draw(1,{marker},o)==nil and P.active_key()==nil)
          o.now=12;local actual,planned=P.draw(1,{marker},o);assert(actual and planned and actual~=planned)
          local f=calls.frames[#calls.frames];assert(actual.x==f.x and actual.y==f.y and actual.w==f.w and actual.h==f.h)
          assert(actual.x>=4 and actual.y>=4 and actual.x+actual.w<=423 and actual.y+actual.h<=236)
          assert(P.last_bounds().actual==actual and P.last_bounds().planned==planned and not P.last_bounds().overflow)
          assert(P.active_key()=='corner')
          o.hidden=true;assert(P.draw(1,{marker},o)==nil and P.active_key()==nil and P.last_bounds()==nil)
        end end
        ''')


if __name__ == '__main__':
    unittest.main()
