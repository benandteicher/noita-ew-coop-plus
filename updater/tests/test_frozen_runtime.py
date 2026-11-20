"""Frozen packaging trust boundary; synthetic data only, never executes binaries."""
import importlib.util,json,pathlib,tempfile,unittest
spec=importlib.util.spec_from_file_location('frozen_builder',pathlib.Path(__file__).resolve().parents[1]/'release_builder.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class FrozenTests(unittest.TestCase):
 def fixture(self,folder):
  required=['proxy/noita_proxy.exe','proxy/steam_api64.dll','mod/quant.ew/ewext.dll','mod/quant.ew/init.lua','mod/quant.ew/mod.xml','mod/quant.ew/NoitaPatcher/noitapatcher.dll','mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll',*['proxy/'+n for n in b.LICENSE_NAMES]]
  rows=[]
  for index,name in enumerate(required):
   p=folder/str(index);p.write_bytes(name.encode());rows.append(dict(path=name,source=str(p),size=p.stat().st_size,sha256=b.sha(p.read_bytes())))
  d=dict(schema=1,runtimeId='explicit-test',diagnosticAdapter=False,files=rows,sourceHashes={'src.rs':'f'*64},buildCommands={'proxy':'explicit source build'},gameFiles=[dict(path=n,size=1,sha256='a'*64)for n in('noita.exe','data/data.wak')]);p=folder/'spec.json';p.write_text(json.dumps(d));return p,d
 def test_exact_explicit_components_and_missing_native(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,d=self.fixture(pathlib.Path(tmp));entries,_=b.frozen_entries(p,b'updater');self.assertEqual(entries['proxy/noita_proxy.exe'],b'proxy/noita_proxy.exe');d['files']=[x for x in d['files']if x['path']!='mod/quant.ew/ewext.dll'];p.write_text(json.dumps(d))
   with self.assertRaisesRegex(ValueError,'incomplete'):b.frozen_entries(p,b'updater')
 def test_hash_corruption_cannot_substitute(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,d=self.fixture(pathlib.Path(tmp));pathlib.Path(d['files'][0]['source']).write_bytes(b'official replacement')
   with self.assertRaisesRegex(ValueError,'hash mismatch'):b.frozen_entries(p,b'updater')
 def test_game_or_private_payload_and_adapter_refused(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,d=self.fixture(pathlib.Path(tmp));row=dict(d['files'][0]);row['path']='proxy/noita.exe';d['files'].append(row);p.write_text(json.dumps(d))
   with self.assertRaisesRegex(ValueError,'Licensed/private'):b.frozen_entries(p,b'updater')
   d['files'].pop();d['diagnosticAdapter']=True;p.write_text(json.dumps(d))
   with self.assertRaisesRegex(ValueError,'non-diagnostic'):b.frozen_entries(p,b'updater')
 def test_duplicate_case_and_missing_engine_fingerprints_refused(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,d=self.fixture(pathlib.Path(tmp));row=dict(d['files'][0]);row['path']='proxy/NOITA_PROXY.EXE';d['files'].append(row);p.write_text(json.dumps(d))
   with self.assertRaisesRegex(ValueError,'Invalid frozen'):b.frozen_entries(p,b'updater')
   d['files'].pop();d['gameFiles']=[];p.write_text(json.dumps(d))
   with self.assertRaisesRegex(ValueError,'fingerprints'):b.frozen_entries(p,b'updater')
if __name__=='__main__':unittest.main()
