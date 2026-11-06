"""Read-only spell info and nested captured-slot GUI contracts in LuaJIT."""
import os
from pathlib import Path
import struct
import re
import unittest

import test_ping_wands as wand_fixture

ROOT = Path(__file__).resolve().parents[1]


def installed_files():
    path = Path(os.environ.get('NOITA_DATA_WAK', r'D:\SteamLibrary\steamapps\common\Noita\data\data.wak'))
    if not path.exists():
        return None
    data = path.read_bytes()
    pos, result = 16, {}
    for _ in range(struct.unpack_from('<I', data, 4)[0]):
        offset, size, length = struct.unpack_from('<III', data, pos)
        pos += 12
        name = data[pos:pos+length].decode()
        pos += length
        if name.endswith(('.lua', '.xml')):
            result[name] = data[offset:offset+size].decode('utf-8', 'replace')
    return result


class PingSpellInfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = installed_files()

    def runtime(self, native=False):
        lua = wand_fixture.PingWandTests().runtime()
        if native:
            if self.game is None:
                self.skipTest('Installed Noita source unavailable')
            lua.globals().READ_GAME = lambda p: self.game.get(p, '')
            lua.execute('function ModTextFileGetContent(p)return READ_GAME(p)end')
            # Create closures from native definitions in an empty environment.
            # The returned action functions are never invoked; source/line metadata
            # matches the real loader for provenance checks without engine effects.
            source=self.game['data/scripts/gun/gun_actions.lua'].replace('\r\n','\n').replace('\r','\n')
            definitions=[]
            for spell in ('LIGHT_BULLET','DAMAGE','TELEPORT_PROJECTILE','BOMB','SPEED','SPITTER','RECHARGE'):
                start=re.search(r'\n\s*id\s*=\s*"'+spell+r'"',source).start()
                function=re.search(r'action\s*=\s*(function\s*\(\))',source[start:])
                fstart=start+function.start(1)
                ending=re.search(r'\n\s*end\s*,',source[fstart:])
                fend=fstart+ending.end()-1
                definitions.append((spell,source[:fstart].count('\n'),source[fstart:fend]))
            lua.globals().DEFINITIONS=lua.table_from([lua.table_from(d) for d in definitions])
            lua.execute('''
            actions={}
            for _,d in ipairs(DEFINITIONS)do
              local loader=assert(loadstring(string.rep('\\n',d[2])..'return '..d[3],'@data/scripts/gun/gun_actions.lua'))
              setfenv(loader,{})
              actions[#actions+1]={id=d[1],action=loader()}
            end
            ''')
        lua.execute('''
        I=dofile_once('mods/quant.ew/files/system/player_ping/spell_info.lua');I.clear()
        T=dofile_once('mods/quant.ew/files/system/player_ping/spell_tooltip.lua')
        ''')
        return lua

    def test_installed_native_projectile_modifier_teleport_bomb_and_alwayscast_speed(self):
        self.runtime(native=True).execute('''
        local before_c,before_actions=c,actions
        local bolt=assert(I.lookup('LIGHT_BULLET',-1))
        assert(bolt.values.projectile_damage==3 and bolt.values.mana==5)
        assert(bolt.values.cast_delay==.05 and bolt.values.spread==-1 and bolt.values.critical_chance==5)
        assert(bolt.values.projectile_speed==800 and bolt.values.projectile_speed_range[1]==750 and bolt.values.projectile_speed_range[2]==850)
        local damage=assert(I.lookup('DAMAGE',-1))
        assert(damage.values.bonus_projectile==10 and damage.values.projectile_damage==nil)
        assert(damage.values.cast_delay==5/60 and damage.values.mana==5)
        local teleport=assert(I.lookup('TELEPORT_PROJECTILE',-1))
        assert(teleport.values.projectile_damage==0 and teleport.values.mana==40 and teleport.values.spread==-2)
        local bomb=assert(I.lookup('BOMB',3))
        assert(bomb.values.explosion_damage==125 and bomb.values.explosion_radius==60 and bomb.values.uses==3)
        assert(bomb.values.cast_delay==100/60 and bomb.values.mana==25)
        local many=I.lookup('BOMB',123456)
        for _,row in ipairs(many.rows)do if row.key=='uses'then assert(row.value=='123456')end end
        local speed=assert(I.lookup('SPEED',-1))
        assert(speed.values.speed_multiplier==2.5 and speed.values.mana==3 and speed.values.uses==-1)
        assert(c==before_c and actions==before_actions)
        ''')

    def test_localized_metadata_and_strictly_no_action_evaluation_or_dynamic_guessing(self):
        lua = self.runtime()
        lua.globals().READ_GAME = lambda p: '''
actions={
 {
 id="DYNAMIC",name="$dyn_name",description="$dyn_desc",sprite="data/ui_gfx/gun_actions/bomb.png",
 type=ACTION_TYPE_MODIFIER,mana=7,action=function()
 EntityLoad("bad");if Random(0,1)==1 then c.damage_projectile_add=c.damage_projectile_add+99 end
 end},
}
''' if p.endswith('gun_actions.lua') else ''
        lua.execute('''
        function ModTextFileGetContent(p)return READ_GAME(p)end
        touched=0;actions={{id='DYNAMIC',name='$dyn_name',description='$dyn_desc',mana=7,
          action=function()touched=touched+1;error('must never execute')end}}
        local old=GameTextGetTranslatedOrNot
        function GameTextGetTranslatedOrNot(s)
          if s=='$dyn_name'then return 'Localized name'elseif s=='$dyn_desc'then return 'Localized description'
          elseif s=='$inventory_actiontype_modifier'then return 'Localized modifier'end;return old(s)
        end
        local info=assert(I.lookup('DYNAMIC',2))
        assert(info.name=='Localized name' and info.description=='Localized description')
        assert(info.rows[1].value=='Localized modifier' and info.values.mana==7 and info.values.uses==2)
        assert(info.values.bonus_projectile==nil and info.note:find('unavailable',1,true) and touched==0)
        ''')

    def test_unknown_id_is_distinct_and_empty_or_unsafe_id_has_no_child(self):
        self.runtime().execute('''
        local missing=assert(I.lookup('NONEXISTENT_SPELL',0))
        assert(missing.unknown and missing.name=='NONEXISTENT_SPELL' and #missing.rows==0)
        assert(missing.icon=='data/ui_gfx/gun_actions/unidentified.png')
        assert(I.lookup('',0)==nil and I.lookup('../BOMB',3)==nil and I.lookup(string.rep('A',65),0)==nil)
        ''')

    def test_same_native_source_and_lines_with_modified_function_cannot_borrow_stats(self):
        self.runtime(native=True).execute('''
        assert(I.lookup('DAMAGE',-1).values.bonus_projectile==10)
        for _,d in ipairs(DEFINITIONS)do if d[1]=='DAMAGE'then
          local changed=d[3]:gsub('0%.4','0.4 * 999',1)
          local loader=assert(loadstring(string.rep('\\n',d[2])..'return '..changed,'@data/scripts/gun/gun_actions.lua'))
          setfenv(loader,{})
          for _,a in ipairs(actions)do if a.id=='DAMAGE'then a.action=loader()end end
        end end
        local info=I.lookup('DAMAGE',-1)
        assert(info.values.bonus_projectile==nil and info.values.cast_delay==nil)
        assert(info.note:find('unavailable',1,true))
        ''')

    def test_runtime_type_is_authoritative_unknown_stays_unknown_missing_uses_native(self):
        self.runtime(native=True).execute('''
        local action
        for _,a in ipairs(actions)do if a.id=='LIGHT_BULLET'then action=a;break end end
        assert(action)
        action.type=ACTION_TYPE_MODIFIER
        local changed=I.lookup('LIGHT_BULLET',-1)
        assert(changed.values.type=='MODIFIER' and changed.rows[1].value=='Proj. modifier')
        action.type=999
        local unknown=I.lookup('LIGHT_BULLET',-1)
        assert(unknown.values.type==nil and unknown.rows[1].value=='Unknown')
        action.type=nil
        local original=I.lookup('LIGHT_BULLET',-1)
        assert(original.values.type=='PROJECTILE' and original.rows[1].value=='Projectile')
        ''')

    def static_runtime(self, body):
        lua=self.runtime()
        source='''actions={
 {
 id="TEST_ACTION",name="Test",description="",type=ACTION_TYPE_MODIFIER,mana=3,
 action=function()
'''+body+'''
 end,
 },
}
'''
        start=source.index('function()')
        end=source.index('\n end,',start)+len('\n end')
        lua.globals().READ_GAME=lambda p: source if p.endswith('gun_actions.lua') else ''
        lua.globals().DEFINITION=source[start:end]
        lua.globals().PREFIX_LINES=source[:start].count('\n')
        lua.execute('''
        function ModTextFileGetContent(p)return READ_GAME(p)end
        local loader=assert(loadstring(string.rep('\\n',PREFIX_LINES)..'return '..DEFINITION,'@data/scripts/gun/gun_actions.lua'))
        setfenv(loader,{})
        actions={{id='TEST_ACTION',action=loader()}}
        I.clear()
        ''')
        return lua

    def test_unhandled_static_mutations_and_calls_are_explicitly_partial(self):
        supported='''c.fire_rate_wait=c.fire_rate_wait+2
c.damage_projectile_add=c.damage_projectile_add+0.1
draw_actions(1,true)'''
        baseline=self.static_runtime(supported)
        baseline.execute("local x=I.lookup('TEST_ACTION',-1);assert(not x.partial and x.values.bonus_projectile==2.5 and x.values.cast_delay==2/60)")
        for mutation in (
            'c.bounces=c.bounces+1',
            'c.lifetime_add=c.lifetime_add+60',
            'c.extra_entities=c.extra_entities.."data/entities/misc/homing.xml,"',
            'current_reload_time=current_reload_time*0.5',
            'c.spread_degrees=c.spread_degrees*2',
            'c.speed_multiplier=c.speed_multiplier*1.2',
            'shot_effects.recoil_knockback=shot_effects.recoil_knockback+10',
            'some_effect()',
            'draw_actions(3,true)',
        ):
            with self.subTest(mutation=mutation):
                lua=self.static_runtime(supported+'\n'+mutation)
                lua.execute('''
                local x=I.lookup('TEST_ACTION',-1)
                assert(x.partial and x.note:find('Partial',1,true) and x.values.bonus_projectile==2.5)
                assert(x.values.bounces==nil and x.values.lifetime_add==nil and x.values.speed_multiplier==nil)
                ''')

    def test_installed_known_partial_effects_keep_verified_values_and_honest_notice(self):
        self.runtime(native=True).execute('''
        local damage=I.lookup('DAMAGE',-1)
        assert(damage.partial and damage.values.bonus_projectile==10)
        assert(not I.lookup('LIGHT_BULLET',-1).partial) -- native has no screen-shake row
        assert(not I.lookup('TELEPORT_PROJECTILE',-1).partial)
        assert(not I.lookup('BOMB',3).partial)
        assert(not I.lookup('SPEED',-1).partial)
        ''')

    def test_constrained_height_overflow_row_is_measured_nonoverlapping_and_inside_frame(self):
        self.runtime().execute('''
        local info={name='Fields',description='',note='',icon='data/ui_gfx/gun_actions/bomb.png',rows={}}
        for i=1,20 do info.rows[i]={label='A long label '..i,value='A long value '..i}end
        local plan=assert(T.measure(1,info,{max_width=118,max_height=70}))
        local overflow=plan.rows[#plan.rows]
        assert(overflow.overflow and overflow.vw==GuiGetTextDimensions(1,overflow.value))
        assert(GuiGetTextDimensions(1,overflow.label)+8+overflow.vw<=plan.content)
        calls.widgets={};calls.text={}
        local box=assert(T.draw(1,{x=30,y=30,w=10,h=10},info,{w=126,h=78,plan=plan}))
        assert(box.w<=118 and box.h<=70 and box.x>=4 and box.y>=4 and box.x+box.w<=122 and box.y+box.h<=74)
        for _,b in ipairs(calls.widgets)do
          assert(b.dx>=box.x and b.dy>=box.y and b.dx+b.dw<=box.x+box.w and b.dy+b.dh<=box.y+box.h)
        end
        local last_label=calls.text[#calls.text-1];local last_value=calls.text[#calls.text]
        assert(last_label.y==last_value.y and last_label.x+GuiGetTextDimensions(1,last_label.text)+8<=last_value.x)
        ''')

    def test_numeric_rhs_requires_whole_literal_not_expression_or_exponent_prefix(self):
        if self.game is None:
            self.skipTest('Installed Noita source unavailable')
        for suffix in (' * 2', 'e-3'):
            lua=self.runtime(native=True)
            original=self.game['data/scripts/gun/gun_actions.lua']
            changed=re.sub(r'(c\.damage_projectile_add\s*=\s*c\.damage_projectile_add\s*\+\s*0\.4)(?!\d)',
                           lambda m:m[1]+suffix,original,count=1)
            lua.globals().READ_GAME=lambda p,changed=changed: changed if p.endswith('gun_actions.lua') else self.game.get(p,'')
            lua.globals().SUFFIX=suffix
            lua.execute('''
            for _,d in ipairs(DEFINITIONS)do if d[1]=='DAMAGE'then
              local changed=d[3]:gsub('0%.4','0.4'..SUFFIX,1)
              local loader=assert(loadstring(string.rep('\\n',d[2])..'return '..changed,'@data/scripts/gun/gun_actions.lua'))
              setfenv(loader,{})
              for _,a in ipairs(actions)do if a.id=='DAMAGE'then a.action=loader()end end
            end end
            I.clear();local info=I.lookup('DAMAGE',-1)
            assert(info.values.bonus_projectile==nil and info.note:find('unavailable',1,true))
            ''')

    def test_inline_native_speed_cap_change_is_not_misreported_as_vanilla_cap(self):
        lua=self.runtime(native=True)
        original=self.game['data/scripts/gun/gun_actions.lua']
        start=re.search(r'\n\s*id\s*=\s*"SPEED"',original).start()
        end=re.search(r'\n\s*id\s*=',original[start+1:]).start()+start+1
        modified=original[:start]+original[start:end].replace('20','30')+original[end:]
        lua.globals().READ_GAME=lambda p: modified if p.endswith('gun_actions.lua') else self.game.get(p,'')
        lua.execute('''
        for _,d in ipairs(DEFINITIONS)do if d[1]=='SPEED'then
          local loader=assert(loadstring(string.rep('\\n',d[2])..'return '..d[3]:gsub('20','30'),'@data/scripts/gun/gun_actions.lua'))
          setfenv(loader,{})
          for _,a in ipairs(actions)do if a.id=='SPEED'then a.action=loader()end end
        end end
        I.clear();local info=I.lookup('SPEED',-1)
        assert(info.values.speed_multiplier==nil and info.note:find('unavailable',1,true))
        ''')

    def test_child_content_sizing_and_utf8_title_fitting(self):
        lua=self.runtime(native=True)
        lua.globals().LONG_TITLE='界é'*100
        lua.execute('''
        local info=I.lookup('DAMAGE',-1)
        local plan=assert(T.measure(1,info,{max_width=419,max_height=232}))
        assert(plan.width<240 and plan.width>=110)
        info.name=LONG_TITLE
        fitted=assert(T.measure(1,info,{max_width=150,max_height=232})).title
        assert(fitted:sub(-3)=='...' and GuiGetTextDimensions(1,fitted)<=116)
        ''')
        lua.eval('fitted').encode('utf-8').decode('utf-8')

    def test_actual_slot_rects_cover_only_occupied_normal_and_alwayscast_icons(self):
        self.runtime().execute('''
        local d=sample(4);local plan=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        local box,returned_plan=E.RenderTooltip(9,9,d,1,-100,plan)
        assert(returned_plan==plan and #box.slot_rects==2)
        local normal,ac
        for _,s in ipairs(box.slot_rects)do
          if s.always_cast then ac=s else normal=s end
          assert(s.x>=box.x and s.y>=box.y and s.x+s.w<=box.x+box.w and s.y+s.h<=box.y+box.h)
        end
        assert(normal.spell_id=='LIGHT_BULLET' and normal.slot==1 and normal.uses==0)
        assert(ac.spell_id=='BLACK_HOLE' and ac.slot==1 and ac.uses==2)
        d.spells={'','','',''};d.always_cast_spells={};d.always_cast_uses={}
        plan=assert(E.MeasureTooltip(1,d,{max_width=419,max_height=232}))
        assert(#E.RenderTooltip(9,9,d,1,-100,plan).slot_rects==0)
        ''')

    def test_nested_child_hover_retains_parent_empty_does_nothing_and_leave_clears(self):
        self.runtime(native=True).execute('''
        local d=sample(4);d.always_cast_spells={'SPEED'};d.always_cast_uses={-1}
        local marker={key='nested',sender='A',core_box={x=100,y=100,w=7,h=7},x=100,y=100,w=7,h=7,
          record={seq=1,wand=d}}
        local o={now=0,w=427,h=240,mx=103,my=103,body=1}
        P.draw(1,{marker},o);o.now=12;local card=assert(P.draw(1,{marker},o))
        local normal,ac
        for _,s in ipairs(card.slot_rects)do if s.always_cast then ac=s else normal=s end end
        o.mx=normal.x+normal.w/2;o.my=normal.y+normal.h/2;o.now=13
        assert(P.draw(1,{marker},o));local child=assert(P.last_bounds().child)
        assert(child.x>=4 and child.y>=4 and child.x+child.w<=423 and child.y+child.h<=236)
        o.mx=child.x+child.w/2;o.my=child.y+child.h/2;o.now=14
        assert(P.draw(1,{marker},o)and P.active_key()=='nested' and P.last_bounds().child)
        -- The coordinator retains a stationary child briefly across passage.
        o.mx=card.x+10;o.my=card.y+10;o.now=15
        assert(P.draw(1,{marker},o)and P.last_bounds().child)
        o.now=31;assert(P.draw(1,{marker},o)and not P.last_bounds().child)
        o.mx=ac.x+ac.w/2;o.my=ac.y+ac.h/2;o.now=32
        assert(P.draw(1,{marker},o)and P.last_bounds().child)
        o.mx=426;o.my=239;o.now=48
        assert(P.draw(1,{marker},o));o.now=64
        assert(P.draw(1,{marker},o)==nil and not P.active_key() and not P.last_bounds())
        ''')

    def test_child_draw_bounds_native_insets_all_corners_and_long_localized_strings(self):
        self.runtime(native=True).execute('''
        FRAME_INSET=2
        local info=I.lookup('BOMB',3);info.name=string.rep('Long name ',30);info.description=string.rep('Long localized description ',30)
        for _,s in ipairs({{4,4},{410,4},{4,225},{410,225}})do
          calls.widgets={}
          local box=T.draw(1,{x=s[1],y=s[2],w=10,h=10},info,{w=427,h=240,parent={x=100,y=30,w=180,h=180}})
          assert(box and box.x>=4 and box.y>=4 and box.x+box.w<=423 and box.y+box.h<=236)
          for _,b in ipairs(calls.widgets)do
            -- The one transparent metric widget is outside the child by design.
            if not(b.x==5 and b.y==5 and b.w==20 and b.h==20)then
              assert(b.dx>=box.x-.001 and b.dy>=box.y-.001 and b.dx+b.dw<=box.x+box.w+.001 and b.dy+b.dh<=box.y+box.h+.001)
            end
          end
        end
        assert(T.measure(1,info,{max_width=80,max_height=50})==nil)
        ''')


if __name__ == '__main__':
    unittest.main()
