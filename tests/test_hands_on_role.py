import unittest,tempfile,json,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class RoleTests(unittest.TestCase):
 def test_client_is_main_audible_and_host_runs_quietly(self):
  spec=importlib.util.spec_from_file_location('roles',ROOT/'diagnostics/input_checkpoint/prepare_hands_on.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory()as tmp:
   d=Path(tmp)
   for role in('host','client'):
    (d/role/'save_shared').mkdir(parents=True)
    (d/role/'save_shared/config.xml').write_text('<Config fullscreen="0" window_w="1" window_h="1" sounds="0" application_pause_when_unfocused="1" mouse_capture_inside_window="1"/>')
    (d/role/'proxy-settings.ron').write_text('(app:(nickname:Some("old")))')
   (d/'manual-audit.json').write_text(json.dumps({r:{'config':{}}for r in('host','client')}))
   m.configure_main_role(d)
   a=json.loads((d/'manual-audit.json').read_text())
   self.assertTrue(a['client']['main_human_window']);self.assertFalse(a['host']['main_human_window'])
   self.assertEqual(a['client']['config']['fullscreen'],'2');self.assertEqual(a['client']['config']['sounds'],'1')
   self.assertEqual(a['host']['config']['fullscreen'],'0');self.assertEqual(a['host']['config']['sounds'],'0')
   self.assertEqual(a['host']['config']['application_pause_when_unfocused'],'0')
   self.assertIn('Recovery-CLIENT-MAIN',(d/'client/proxy-settings.ron').read_text())
if __name__=='__main__':unittest.main()
