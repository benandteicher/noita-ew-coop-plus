// Fresh-clone preference fixtures only. No game/proxy or normal profile is opened.
using System;
using System.IO;
using System.Text;
using System.Xml;
using CoopPlusPlaytest;

public static class PlaytestPreferenceTests {
    static int passed;
    static readonly int[][] NativeModeFixture={new[]{800,600},new[]{1366,768},new[]{1920,1080},new[]{2560,1440},new[]{3840,2160}};
    static void Check(bool okay,string name){if(!okay)throw new Exception(name);Console.WriteLine("PASS "+name);passed++;}
    static void Refuses(Action action,string name){try{action();}catch(Exception){Check(true,name);return;}throw new Exception("Not refused: "+name);}
    static void Text(string file,string content){Directory.CreateDirectory(Path.GetDirectoryName(file));File.WriteAllText(file,content,new UTF8Encoding(false));}
    static XmlElement Config(string file){var document=new XmlDocument{XmlResolver=null};document.Load(file);return document.DocumentElement;}
    static string Initialize(string scratch,string name,string portableXml,string defaultXml,int width=1920,int height=1080,int[][] modes=null){string folder=Path.Combine(scratch,name),game=Path.Combine(folder,"private game"),owned=Path.Combine(folder,"owned portable"),normal=Path.Combine(folder,"default preferences","config.xml");Directory.CreateDirectory(game);Directory.CreateDirectory(owned);if(portableXml!=null)Text(Path.Combine(owned,"save_shared","config.xml"),portableXml);if(defaultXml!=null)Text(normal,defaultXml);Playtest.InitializeFreshPreferences(game,owned,normal,width,height,modes??NativeModeFixture);return game;}
    static void Size(XmlElement config,int width,int height,string name){Check(config.GetAttribute("is_default_config")=="0"&&config.GetAttribute("fullscreen")=="0"&&config.GetAttribute("window_w")==width.ToString()&&config.GetAttribute("window_h")==height.ToString()&&config.GetAttribute("backbuffer_width")==width.ToString()&&config.GetAttribute("backbuffer_height")==height.ToString(),name);}
    public static int Main(string[] args){try {
        if(args.Length!=1||Directory.Exists(args[0]))throw new Exception("Pass a new safe absolute scratch folder.");string scratch=Playtest.Canon(args[0]);Directory.CreateDirectory(scratch);
        Check(Playtest.PreferenceDisplayModeSize()==220,"Unicode DEVMODEW record has the exact 220 byte layout");
        string absent=Initialize(scratch,"Absent",null,null);Size(Config(Path.Combine(absent,"save_shared","config.xml")),1366,768,"genuinely absent preferences select an actual supported visible mode");
        Check(Config(Path.Combine(absent,"save_shared","config.xml")).GetAttribute("config_format_version")=="14","fresh preferences declare the frozen engine's current config format");
        string explicitOverride=Initialize(scratch,"Default marker",null,"<Config is_default_config=\"1\" internal_size_w=\"1280\" internal_size_h=\"720\" />");var explicitConfig=Config(Path.Combine(explicitOverride,"save_shared","config.xml"));Check(explicitConfig.GetAttribute("is_default_config")=="0"&&!explicitConfig.HasAttribute("internal_size_w")&&!explicitConfig.HasAttribute("internal_size_h"),"fresh preferences are explicit overrides without internal-size changes");
        string sparse=Initialize(scratch,"Sparse",null,"<Config language=\"en\" />");var sparseConfig=Config(Path.Combine(sparse,"save_shared","config.xml"));Size(sparseConfig,1366,768,"sparse default preferences receive a real supported mode");Check(sparseConfig.GetAttribute("language")=="en","safe sparse preference retained");
        string actual=Initialize(scratch,"Real keys",null,"<Config window_w=\"1366\" window_h=\"768\" backbuffer_width=\"3840\" backbuffer_height=\"2160\" audio_music_volume=\"0.6\" audio_effects_volume=\"0.35\" audio_sfx_volume=\"0.99\" fullscreen=\"1\" joysticks_enabled=\"0\" mouse_capture_inside_window=\"1\" seed=\"100\"><KeyboardControls key_left=\"other\"/><ModSettings run=\"private\"/></Config>");var actualConfig=Config(Path.Combine(actual,"save_shared","config.xml"));Size(actualConfig,1366,768,"supported real window preferences win over oversized backbuffer and remain windowed");Check(actualConfig.GetAttribute("audio_effects_volume")=="0.35"&&actualConfig.GetAttribute("audio_music_volume")=="0.6"&&!actualConfig.HasAttribute("audio_sfx_volume"),"real effects-volume key copied without obsolete alias");Check(!actualConfig.HasAttribute("joysticks_enabled")&&!actualConfig.HasAttribute("mouse_capture_inside_window")&&!actualConfig.HasAttribute("seed")&&actualConfig.ChildNodes.Count==0,"no controller cursor controls run or mod-settings policy imported");
        string portable=Initialize(scratch,"Portable precedence","<Config window_w=\"800\" window_h=\"600\" audio_effects_volume=\"0.25\" />","<Config window_w=\"1366\" window_h=\"768\" audio_effects_volume=\"0.9\" />");Size(Config(Path.Combine(portable,"save_shared","config.xml")),800,600,"supported selected portable preference mode takes precedence");Check(Config(Path.Combine(portable,"save_shared","config.xml")).GetAttribute("audio_effects_volume")=="0.25","portable safe audio preference takes precedence");
        string backbuffer=Initialize(scratch,"Backbuffer only",null,"<Config backbuffer_width=\"1366\" backbuffer_height=\"768\" />");Size(Config(Path.Combine(backbuffer,"save_shared","config.xml")),1366,768,"supported backbuffer-only config seeds both dimension pairs");
        string large=Initialize(scratch,"Oversized",null,"<Config window_w=\"9223372036854775807\" window_h=\"9223372036854775807\" />",1280,720);Size(Config(Path.Combine(large,"save_shared","config.xml")),800,600,"oversized dimensions select only an actual mode fitting the working area");
        string unsupported=Initialize(scratch,"Unsupported custom",null,"<Config window_w=\"1024\" window_h=\"576\" backbuffer_width=\"1280\" backbuffer_height=\"720\" />");Size(Config(Path.Combine(unsupported,"save_shared","config.xml")),1366,768,"unsupported custom modes fall back to the closest supported safe mode");
        Refuses(()=>Initialize(scratch,"No visible mode",null,null,800,600),"too-small working area refuses instead of inventing a custom mode");
        Refuses(()=>Initialize(scratch,"DTD", "<!DOCTYPE Config [<!ENTITY x SYSTEM 'file:///never-read'>]><Config language=\"&x;\" />","<Config />"),"DTD portable source refused instead of falling back");
        Refuses(()=>Initialize(scratch,"Corrupt portable","<Config>","<Config />"),"corrupt preferred portable source refused instead of falling back");
        Refuses(()=>Initialize(scratch,"Wrong root",null,"<World seed=\"11\"/>"),"wrong preference XML root refused");
        Refuses(()=>Initialize(scratch,"Too large",null,"<Config language=\""+new string('x',1024*1024)+"\"/>"),"oversized source refused");
        Refuses(()=>Initialize(scratch,"Invalid dimensions",null,"<Config window_w=\"bad\" window_h=\"720\"/>"),"corrupt dimensions refused");
        string retained=Path.Combine(actual,"save_shared","config.xml"),before=File.ReadAllText(retained);Text(Path.Combine(Path.GetDirectoryName(actual),"ready.json"),"retained-ready-fixture");
        Refuses(()=>Playtest.InitializeFreshPreferences(actual,Path.Combine(Path.GetDirectoryName(actual),"owned portable"),Path.Combine(Path.GetDirectoryName(actual),"default preferences","config.xml"),1920,1080,NativeModeFixture),"fresh initializer refuses an established preference target");Check(before==File.ReadAllText(retained),"retained preference bytes are never rewritten");
        Console.WriteLine("RESULT "+passed+" focused cases passed");return 0;
    }catch(Exception error){Console.Error.WriteLine(error);return 1;}}
}
