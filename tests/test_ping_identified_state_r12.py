"""New categories use existing bounded event/gesture rules, no game entities."""
import unittest
import test_ping_usability_lifecycle as fixture

class IdentificationStateTests(unittest.TestCase):
 def runtime(self):
  l=fixture.runtime();l.execute('''
  function object_packet(id,category,key,seq,kind)
    local p=packet(id,kind or category,10,20,id,'world');p.seq=seq or 1
    p.ident={v=1,category=category,id=key}
    return p
  end
  ''');return l
 def test_session_spells_items_static_enemy_thirty_seconds_and_danger_retains_identity(self):
  self.runtime().execute('''
  local spell=assert(m.accept(s,'A',object_packet(1,'spell','SPITTER'),0))
  local item=assert(m.accept(s,'A',object_packet(2,'item','potion'),0))
  local p=object_packet(3,'enemy','zombie');local enemy=assert(m.accept(s,'A',p,0))
  assert(spell.expires==math.huge and item.expires==math.huge and enemy.expires==1800)
  m.update(s,1000);assert(enemy.x==10 and enemy.y==20)
  p.seq=2;p.kind='danger';p.life=120;local changed,event=m.accept(s,'A',p,1001)
  assert(changed==enemy and event=='update'and enemy.ident.category=='enemy'and enemy.ident.id=='zombie')
  assert(enemy.expires==1121);m.update(s,1121);assert(not s.records[enemy.key])
  p.seq=99;assert(not m.accept(s,'A',p,1122))
  ''')
 def test_metadata_shape_kind_source_and_retained_copy_are_bounded(self):
  self.runtime().execute('''
  local p=object_packet(1,'item','potion');local r=assert(m.accept(s,'A',p,0))
  p.ident.id='heart';assert(r.ident.id=='potion')
  for _,bad in ipairs({{v=1,category='item',id='../potion'},{v=1,category='item',id='potion',icon='evil'},
    {v=1,category='item',id='potion',name='fake'},{v=1,category='item',id=3},
    {v=2,category='item',id='potion'},{v=1,category='other',id='potion'}})do
    p.id=2;p.target=2;p.ident=bad;assert(not m.accept(s,'A',p,1))
  end
  p=object_packet(2,'spell','SPITTER');p.kind='item';assert(not m.accept(s,'A',p,1))
  p.kind='spell';p.source='held';assert(not m.accept(s,'A',p,1))
  p.source='world';p.ident=nil;assert(not m.accept(s,'A',p,1))
  p=object_packet(2,'enemy','zombie');p.life=500;assert(not m.accept(s,'A',p,1))
  ''')
 def test_quota_and_cancel_use_normal_world_retirement_not_new_category_accounts(self):
  self.runtime().execute('''
  for id=1,11 do
    local kind=id%2==0 and 'spell'or 'item';local key=kind=='spell'and 'SPITTER'or 'potion'
    assert(m.accept(s,'A',object_packet(id,kind,key),id))
  end
  assert(#m.list(s)==10 and not s.records['A:1:1'])
  local held=packet(12,'wand',0,0,12,'held');assert(m.accept(s,'A',held,12));assert(#m.list(s)==11)
  assert(m.cancel(s,'A',1,11));assert(#m.list(s)==10)
  local stale=object_packet(11,'item','potion',99);assert(not m.accept(s,'A',stale,13))
  ''')
 def test_new_object_doubles_preserve_data_but_existing_wand_gesture_is_unchanged(self):
  self.runtime().execute('''
  for _,category in ipairs({'spell','item','enemy'})do
    g.clear(history);m.reset(s,'run',0)
    local id=category=='spell'and'SPITTER'or category=='item'and'potion'or'zombie'
    local d=g.classify(history,{now=0,x=10,y=20,kind=category,identity='object:1',own='A',records={}})
    assert(d.action=='create');local r=assert(m.accept(s,'A',object_packet(1,category,id),0));g.commit(history,d,r)
    d=g.classify(history,{now=8,x=10,y=20,kind='location',own='A',selected=r,records={r}})
    assert(d.action=='upgrade'and d.kind=='danger'and d.record.ident.id==id)
  end
  ''')
 def test_same_object_refreshes_distinct_nearby_objects_and_plain_location_do_not_merge(self):
  self.runtime().execute('''
  local r=assert(m.accept(s,'A',object_packet(1,'item','potion'),0));identities[1]='object:10'
  local d=g.classify(history,{now=50,x=11,y=20,kind='item',identity='object:11',own='A',records={r},identities=identities})
  assert(d.action=='create')
  d=g.classify(history,{now=50,x=11,y=20,kind='item',identity='object:10',own='A',records={r},identities=identities})
  assert(d.action=='refresh'and d.record==r)
  local p=object_packet(1,'item','potion',2,'danger');p.life=1800;assert(m.accept(s,'A',p,51))
  d=g.classify(history,{now=80,x=11,y=20,kind='location',own='A',records={r},identities=identities})
  assert(d.action=='create')
  d=g.classify(history,{now=80,x=11,y=20,kind='item',identity='object:10',own='A',records={r},identities=identities})
  assert(d.action=='refresh')
  ''')
