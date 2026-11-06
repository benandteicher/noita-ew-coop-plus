"""R9 native spell display contracts, without evaluating any action or entity.

Installed noita.exe: B5F320/B5F2E0 multiply by25 then import lroundf;
B5F360 divides integer frames by60 and calls B5F060 with precision2;
B6C4EC/B6FD8A truncate mana/spread. See SPELL_FORMAT_REFERENCE.md.
"""
from pathlib import Path
import hashlib
import struct
import sys
import unittest

import test_ping_spell_info as fixture

ROOT = Path(__file__).resolve().parents[1]


class NativeSpellFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixture.PingSpellInfoTests()
        cls.fixture.game = fixture.installed_files()

    def runtime(self, native=True):
        return self.fixture.runtime(native=native)

    def test_spitter_rounds_damage_native_but_preserves_raw_and_has_no_lifetime_row(self):
        self.runtime().execute('''
        local x=assert(I.lookup('SPITTER',-1))
        assert(x.values.projectile_damage==7.5 and x.display.projectile_damage=='8')
        assert(x.values.cast_delay==-1/60 and x.display.cast_delay=='-0.02 s')
        assert(x.values.spread==6 and x.display.spread=='+6 DEG')
        assert(x.values.projectile_speed==500 and x.display.projectile_speed=='500')
        assert(x.values.explosion_radius==nil and x.values.lifetime==nil)
        assert(not x.partial and x.note==nil and x.display.uses==nil)
        for _,row in ipairs(x.rows)do
          assert(row.raw==x.values[row.key] and row.value==x.display[row.key])
          assert(row.key~='lifetime' and row.key~='critical_chance')
        end
        ''')

    def test_native_time_integer_and_signed_damage_fields_keep_distinct_rounding(self):
        self.runtime().execute('''
        local bomb=I.lookup('BOMB',3)
        assert(bomb.display.explosion_damage=='125' and bomb.display.explosion_radius=='60')
        assert(bomb.display.cast_delay=='+1.67 s' and bomb.values.cast_delay==100/60)
        assert(bomb.display.uses=='3' and not bomb.note)
        local damage=I.lookup('DAMAGE',-1)
        assert(damage.display.bonus_projectile=='+10' and damage.values.bonus_projectile==10)
        assert(damage.display.cast_delay=='+0.08 s' and damage.values.cast_delay==5/60)
        local recharge=I.lookup('RECHARGE',-1)
        assert(recharge.display.cast_delay=='-0.17 s' and recharge.display.recharge=='-0.33 s')
        local speed=I.lookup('SPEED',-1)
        assert(speed.display.speed_multiplier=='x 2.50' and speed.values.speed_multiplier==2.5)
        assert(not speed.note)
        ''')

    def test_fractional_modifier_rounding_signed_halves_and_localized_unit_templates(self):
        lua=self.fixture.static_runtime('''c.damage_projectile_add=c.damage_projectile_add-0.3
c.fire_rate_wait=c.fire_rate_wait+30
c.spread_degrees=c.spread_degrees-1.9
draw_actions(1,true)''')
        lua.execute('''
        local x=I.lookup('TEST_ACTION',-1)
        assert(x.values.bonus_projectile==-7.5 and x.display.bonus_projectile=='-8')
        assert(x.values.cast_delay==0.5 and x.display.cast_delay=='+0.50 s')
        assert(x.values.spread==-1.9 and x.display.spread=='-1 DEG')
        function GameTextGetTranslatedOrNot(k)
          if k=='$inventory_seconds'then return '$0 秒'
          elseif k=='$inventory_degrees'then return '$0 度'end;return k
        end
        x=I.lookup('TEST_ACTION',-1)
        assert(x.display.cast_delay=='+0.50 秒' and x.display.spread=='-1 度')
        ''')

    def test_current_child_box_is_stationary_with_exact_frame_and_clamps_when_viewport_changes(self):
        self.runtime().execute('''
        FRAME_INSET=3
        local info=I.lookup('SPITTER',-1)
        local slot={x=20,y=30,w=15,h=15}
        local first,plan=T.draw(1,slot,info,{w=427,h=240})
        assert(first and first.w==plan.width and first.h==plan.height)
        local moved={x=350,y=210,w=15,h=15}
        local next_box,next_plan=T.draw(1,moved,info,{w=427,h=240,box=first})
        assert(next_box.x==first.x and next_box.y==first.y)
        assert(next_box.w==next_plan.width and next_box.h==next_plan.height)
        local smaller,new_plan=T.draw(1,moved,info,{w=180,h=180,box={x=400,y=230},plan=plan})
        assert(smaller and smaller.w==new_plan.width and smaller.h==new_plan.height)
        assert(smaller.x>=4 and smaller.y>=4 and smaller.x+smaller.w<=176 and smaller.y+smaller.h<=176)
        ''')

    def test_icons_native_size_groups_and_last_row_bottom_padding_match_actual_extent(self):
        self.runtime().execute('''
        local info=I.lookup('SPITTER',-1)
        local plan=assert(T.measure(1,info,{max_width=419,max_height=232}))
        assert(#plan.note==0)
        local gaps=0
        for _,r in ipairs(plan.rows)do
          assert(r.icon and r.iw==7 and r.ih==7 and r.label_x==10)
          if r.gap>0 then assert(r.gap==plan.step);gaps=gaps+1 end
        end
        assert(gaps==1)
        calls.widgets={};calls.text={};calls.image_widgets={}
        local box=assert(T.draw(1,{x=10,y=10,w=15,h=15},info,{w=427,h=240,plan=plan}))
        for _,widget in ipairs(calls.widgets)do
          assert(widget.dx>=box.x and widget.dy>=box.y)
          assert(widget.dx+widget.dw<=box.x+box.w+.001 and widget.dy+widget.dh<=box.y+box.h+.001)
        end
        local last=calls.text[#calls.text];local _,th=GuiGetTextDimensions(1,last.text)
        assert(box.y+box.h-(last.y+th)==3)
        for _,im in ipairs(calls.image_widgets)do
          if im.path:find('/inventory/icon_',1,true)then assert(im.w==7 and im.h==7)end
        end
        ''')

    def test_installed_machine_code_and_import_prove_damage_rounding(self):
        exe=Path(r'D:/SteamLibrary/steamapps/common/Noita/noita.exe')
        deps=ROOT/'target/hover-tools'
        if not exe.exists() or not deps.exists():
            self.skipTest('Pinned executable/offline PE reader unavailable')
        sys.path.insert(0,str(deps))
        import pefile
        raw=exe.read_bytes()
        if hashlib.sha256(raw).hexdigest()!='808d2a0ab51ea0b46e9ad2aeb3327a4b0ce3feae04f32ba26326bf585b5779bd':
            self.skipTest('Executable differs from the audited version')
        pe=pefile.PE(data=raw);image=pe.get_memory_mapped_image()
        for rva in (0x75F320,0x75F2E0):
            block=image[rva:rva+64]
            self.assertIn(bytes.fromhex('f30f1005c8311501f30f59c1'),block) # load25; mulss xmm0,xmm1
            self.assertIn(bytes.fromhex('ff15f073f000'),block) # imported lroundf
        imports={s.address:s.name for dll in pe.DIRECTORY_ENTRY_IMPORT for s in dll.imports}
        self.assertEqual(imports[0xF073F0],b'lroundf')
        self.assertEqual(struct.unpack_from('<f',image,0x11531C8-0x400000)[0],25)


if __name__=='__main__':
    unittest.main()
