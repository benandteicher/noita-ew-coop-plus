"""Bounded prose-width fallback and balanced headers; no stat extraction changes."""
from pathlib import Path
import unittest
import test_ping_spell_info as fixture

ROOT=Path(__file__).resolve().parents[1]

class SpellLayoutTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):fixture.PingSpellInfoTests.setUpClass()
 def runtime(self):
  l=fixture.PingSpellInfoTests().runtime(native=True)
  l.globals().BEFORE=(ROOT/'diagnostics/ping_v19_r14/before_spell_tooltip.lua').read_text(encoding='utf-8')
  l.execute('''
  Before=assert(loadstring(BEFORE,'@r13_spell_tooltip'))()
  local tr=GameTextGetTranslatedOrNot
  function GameTextGetTranslatedOrNot(k)
   local t={['$action_light_bullet']='Spark bolt',['$actiondesc_light_bullet']='A weak but enchanting sparkling projectile',
    ['$action_damage']='Damage Plus',['$actiondesc_damage']='Increases the damage done by a projectile',
    ['$action_teleport_projectile']='Teleport bolt',['$actiondesc_teleport_projectile']='A magical bolt that moves you wherever it ends up flying'}
   return t[k]or tr(k)
  end
  ''');return l
 def test_description_driven_cards_narrow_and_keep_all_fields_and_raw_data(self):
  self.runtime().execute('''
  for _,id in ipairs({'LIGHT_BULLET','DAMAGE','TELEPORT_PROJECTILE'})do
   local info=I.lookup(id);local old=assert(Before.measure(1,info,{max_width=400,max_height=300}))
   local p=assert(T.measure(1,info,{max_width=400,max_height=300}))
   assert(p.width<old.width and p.content==140 and p.width_policy=='preferred')
   assert(p.omitted_fields==0 and p.field_count==#info.rows and #p.rows==#old.rows)
   for j,r in ipairs(p.rows)do assert(r.label==old.rows[j].label and r.value==old.rows[j].value)end
   assert(info.values.uses==nil)
  end
  ''')
 def test_small_viewport_uses_wider_fallback_instead_of_replacing_a_stat(self):
  self.runtime().execute('''
  local info=I.lookup('LIGHT_BULLET')
  local old=assert(Before.measure(1,info,{max_width=232,max_height=300}))
  local p=assert(T.measure(1,info,{max_width=232,max_height=old.height}))
  local narrow=assert(T.measure(1,info,{max_width=150,max_height=old.height}))
  assert(p.width_policy=='prose_fallback'and p.width>narrow.width)
  assert(p.height<=old.height and p.omitted_fields==0 and narrow.omitted_fields>0)
  assert(p.field_count==#info.rows)
  local b=assert(T.draw(1,{x=235,y=old.height+4,w=7,h=7},info,{w=240,h=old.height+8,plan=p}))
  assert(b.x>=4 and b.y>=4 and b.x+b.w<=236 and b.y+b.h<=old.height+4)
  ''')
 def test_meaningful_stat_columns_can_exceed_prose_and_old_internal_width_cap(self):
  self.runtime().execute('''
  local info={name='Stat',description='Short prose',icon='data/ui_gfx/gun_actions/bomb.png',rows={
   {label='Important localized statistic',value='123456789012345678901234567890'}}}
  local p=assert(T.measure(1,info,{max_width=420,max_height=180}))
  assert(p.width>240 and p.omitted_fields==0)
  assert(p.rows[1].label==info.rows[1].label and p.rows[1].value==info.rows[1].value)
  ''')
 def test_title_is_vertically_centered_against_complete_art_and_description_gap_is_preserved(self):
  self.runtime().execute('''
  local info=I.lookup('LIGHT_BULLET');local p=assert(T.measure(1,info,{max_width=300,max_height=260}))
  local _,lh=GuiGetTextDimensions(1,p.title)
  assert(math.abs(p.title_y+lh/2-(p.icon_y+p.icon_h/2))<=.5)
  assert(p.description_y==p.header_height+4 and p.icon_w==20 and p.icon_h==20)
  calls.text={};calls.image_widgets={}
  local b=assert(T.draw(1,{x=20,y=20,w=7,h=7},info,{w=320,h=280,plan=p}))
  local title=calls.text[1];local art
  for _,a in ipairs(calls.image_widgets)do if a.path:find('item_bg_',1,true)then art=a end end
  assert(art and art.w==20 and art.h==20 and art.scale==1)
  assert(math.abs(title.y+lh/2-(art.y+10))<=.5)
  assert(calls.text[2].y-(art.y+20)==4)
  ''')

if __name__=='__main__':unittest.main()
