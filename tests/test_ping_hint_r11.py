"""The actual removal hint shares label bounds; no core/control behavior changes."""
import unittest
import test_ping_renderer_r2 as fixture

class RemovalHintTests(unittest.TestCase):
 def runtime(self):
  l=fixture.runtime();l.execute('function GuiZSetForNextWidget()end');return l
 def test_prior_overlap_is_reproduced_and_hint_now_has_real_clearance(self):
  self.runtime().execute('''
  opts.mx=200;opts.my=120;opts.local_peer='A';opts.removal_hint='[Middle mouse] Remove ping'
  local m=draw(200);assert(m.name_visible and m.hint_visible)
  local old={x=m.label_boxes.hint.x,y=m.core_box.y-14,w=m.label_boxes.hint.w,h=m.label_boxes.hint.h}
  assert(overlaps(old,m.label_boxes.name))
  assert(not overlaps(m.label_boxes.hint,m.label_boxes.name))
  assert(not overlaps(m.label_boxes.hint,m.core_box))
  local original=renderer.layout(records,opts)[1];assert(samebox(m,original))
  ''')
 def test_edges_shared_rows_names_and_distances_do_not_overlap_hint(self):
  self.runtime().execute('''
  opts.local_peer='A';opts.removal_hint='[Q] Remove ping';record.kind='wand';record.source='held'
  for _,p in ipairs({{0,0},{0,-10000},{0,10000},{-10000,0},{10000,0},{10000,10000},{-10000,-10000}})do
    record.x,record.y=p[1],p[2]
    local before=renderer.layout(records,{view=opts.view,now=200})[1]
    opts.mx,opts.my=before.center_x,before.center_y
    local m=draw(200);assert(m.hint_visible and samebox(m,before))
    local hint=m.label_boxes.hint
    assert(hint.x>=2 and hint.y>=2 and hint.x+hint.w<=398 and hint.y+hint.h<=238)
    for kind,box in pairs(m.label_boxes)do if kind~='hint'then assert(not overlaps(hint,box))end end
  end
  ''')
 def test_foreign_fresh_or_not_directly_hovered_core_gets_no_hint(self):
  self.runtime().execute('''
  opts.local_peer='A';opts.removal_hint='[Q] Remove ping';opts.mx=200;opts.my=120
  assert(not draw(18).hint_visible)
  record.sender='B';assert(not draw(200).hint_visible)
  record.sender='A';opts.mx=2;opts.my=2;opts.selected_key=record.key
  assert(not draw(201).hint_visible)
  ''')
