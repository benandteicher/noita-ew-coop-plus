"""Bounded gold symptom/receipt checks in the existing two-peer substitute.

No assertion here claims native attachment or pickup ordering was reproduced.
"""
import unittest
from test_shared_income import IncomePeers

ALIVE="settings.statue=false;if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end"

def saved(p):
 return p.snapshot()

class GoldMilestoneTests(unittest.TestCase):
 def test_native_wallet_boundary_is_not_a_ledger_boundary(self):
  for collector in('A','B'):
   for amount in(32767,32768,32769):
    with self.subTest(collector=collector,amount=amount):
     p=IncomePeers(ALIVE+f';entities[100].c.WalletComponent.money={amount-10};entities[200].c.WalletComponent.money={amount-10}')
     p.pickup(collector)
     self.assertEqual((p.money('A'),p.money('B')),(amount,amount))

 def test_receipt_pause_survives_cold_continue_and_leaves_future_host_credit_pending(self):
  p=IncomePeers(ALIVE);p.pickup()
  data=saved(p);data['B'].pop('ew_income_owner_v1');data['B'].pop('__body')
  q=IncomePeers(ALIVE+";if PEER=='A'then entities[100].c.WalletComponent.money=110 end",data)
  self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
  self.assertIn('no recovery receipt',q.b.eval('calls.message'))
  q.pickup('A','43')
  self.assertEqual((q.money('A'),q.money('B')),(120,100))
  self.assertEqual(q.ledger('.accounts.B.acked'),1)
  self.assertEqual(q.ledger('.accounts.B.next'),2)
  self.assertEqual(q.ledger('.accounts.B.pending[2].amount'),10)
  # Repeat host join/status delivery cannot silently reset the failed receipt.
  q.a.execute('income.on_should_send_updates()');q.flush();q.tick(60)
  self.assertFalse(q.b.eval('ctx.cap.shared_income.enabled()'))
  self.assertEqual((q.money('A'),q.money('B')),(120,100))

 def test_paused_pickup_detaches_exact_unpaid_gold_without_deletion_or_credit(self):
  p=IncomePeers(ALIVE);p.pickup()
  data=saved(p);data['B'].pop('ew_income_owner_v1');data['B'].pop('__body')
  q=IncomePeers(ALIVE,data)
  q.b.execute('''gold=nugget('9999',10)
  entities[gold].parent=ctx.my_player.entity
  entities[gold].c.ItemComponent.inventory_slot={5,0}
  assert(cross.ew_income_pickup(gold,ctx.my_player.entity)==true)
  assert(entities[gold].alive and not entities[gold].c.ItemComponent.is_pickable)
  assert(EntityGetParent(gold)==0)
  assert(entities[gold].c.ItemComponent.inventory_slot[1]==-1)
  ''')
  # Parent is deliberately seeded to expose adapter behavior, not prove the
  # native engine attaches gold or chooses this slot.

 def test_pending_inventory_seed_retired_by_stable_identity_without_extra_payment(self):
  for collector in('A','B'):
   with self.subTest(collector=collector):
    p=IncomePeers(ALIVE)
    for lua in p.peers.values():lua.execute("gold=nugget('42',10);entities[gold].parent=ctx.my_player.entity;entities[gold].c.ItemComponent.inventory_slot={5,0}")
    p.a.execute('frame=60;income.on_world_update()');p.flush()
    p.peers[collector].execute('cross.ew_income_pickup(gold,ctx.my_player.entity)');p.flush();p.tick(35)
    for lua in p.peers.values():self.assertFalse(lua.eval('entities[gold].alive'))
    self.assertEqual((p.money('A'),p.money('B')),(110,110))

if __name__=='__main__':unittest.main()
