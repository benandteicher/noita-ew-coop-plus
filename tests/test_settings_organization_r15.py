"""Settings relocation preserves stored-value contracts; native UI evidence is separate."""
import json
import unittest
from pathlib import Path
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=Path(__file__).with_name('settings_r14_contract.json')
STUBS='''
MOD_SETTING_SCOPE_NEW_GAME=0; MOD_SETTING_SCOPE_RESTART=1; MOD_SETTING_SCOPE_RUNTIME=2
writes=0
function dofile()end
function GameTextGetTranslatedOrNot(x)return x end
function ModTextFileGetContent()return 'Key_BACKSPACE = 42\\nKey_SPACE = 44\\n' end
function ModSettingSet()writes=writes+1 end
function ModSettingSetNextValue()writes=writes+1 end
function mod_settings_update()end
function ModIsEnabled()return false end
'''
def loaded(source):
 l=LuaRuntime();l.execute(STUBS);l.execute(source);return l

def scalar(v):
 if hasattr(v,'items'):return {str(k):scalar(x)for k,x in v.items()}
 if callable(v):return '<function>'
 return v

def leaves(lua):
 result={}
 for _,cat in lua.globals().mod_settings.items():
  for _,setting in cat.settings.items():
   ident=setting.id
   if ident in result:raise AssertionError('Duplicate setting '+ident)
   result[ident]={k:scalar(v)for k,v in setting.items()if k not in ('ui_name','ui_description')}
 return result

class SettingsTests(unittest.TestCase):
 def setUp(self):self.lua=loaded((ROOT/'quant.ew/settings.lua').read_text(encoding='utf-8'))
 def test_every_id_default_scope_type_and_control_contract_is_unchanged(self):
  self.assertEqual(json.dumps(leaves(self.lua),sort_keys=True),
                   json.dumps(json.loads(CONTRACT.read_text(encoding='utf-8')),sort_keys=True))
 def test_main_switches_and_ping_are_near_top_without_duplicate_ids(self):
  cats=self.lua.globals().mod_settings
  self.assertEqual([x.id for _,x in cats[1].settings.items()],['coop_enabled','shared_income','coop_offer_return','shop_hover'])
  self.assertEqual(cats[2].settings[1].id,'rebind_ping')
  self.assertEqual(cats[3].category_id,'coop_revival')
  self.assertEqual(cats[4].category_id,'coop_recovery_runtime')
  self.assertEqual(cats[5].category_id,'pingplus')
 def test_host_and_local_scope_and_return_explanation_are_visible(self):
  cats=self.lua.globals().mod_settings
  self.assertIn('HOST',cats[1].ui_name)
  for _,x in cats[1].settings.items():self.assertIn('HOST,',x.ui_description)
  self.assertEqual(cats[1].settings[2].ui_name,'Shared income with separate wallets')
  self.assertIn("proxy's pooled/shared gold",cats[1].settings[2].ui_description)
  self.assertIn('Portal-arrival recovery and later-death return are separate',cats[1].settings[3].ui_description)
  self.assertIn('local',cats[2].ui_name)
  self.assertIn('new run',cats[3].ui_name)
  self.assertIn('current run',cats[4].ui_name)
 def test_build_and_update_do_not_write_stored_values_or_migrate(self):
  self.lua.execute('''
  current={['quant.ew.coop_enabled']=true,['quant.ew.coop_statue_percent']=47,['quant.ew.ping_sound']=false,['quant.ew.rebind_ping']='44'}
  next_values={['quant.ew.coop_enabled']=false,['quant.ew.ping_opacity']=83}
  function ModSettingGet(key)return current[key]end
  function ModSettingGetNextValue(key)return next_values[key]end
  local before=mod_settings_version
  ModSettingsUpdate(2)
  assert(mod_settings_version==before and writes==0)
  assert(current['quant.ew.coop_enabled']==true and current['quant.ew.coop_statue_percent']==47)
  assert(current['quant.ew.ping_sound']==false and current['quant.ew.rebind_ping']=='44')
  assert(next_values['quant.ew.coop_enabled']==false and next_values['quant.ew.ping_opacity']==83)
  ''')

if __name__=='__main__':unittest.main()
