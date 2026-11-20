// Maintainer-only code-rendered mock. Never a captured Discord card or game input.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Web.Script.Serialization;

internal static class RenderPreview {
 static Color C(string s){return ColorTranslator.FromHtml(s);}
 static void Round(Graphics g,Rectangle r,int radius,Color color){using(var path=new GraphicsPath()){int d=radius*2;path.AddArc(r.X,r.Y,d,d,180,90);path.AddArc(r.Right-d,r.Y,d,d,270,90);path.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);path.AddArc(r.X,r.Bottom-d,d,d,90,90);path.CloseFigure();using(var brush=new SolidBrush(color))g.FillPath(brush,path);}}
 static void Text(Graphics g,string text,float size,FontStyle style,Color color,float x,float y){using(var font=new Font("Segoe UI",size,style,GraphicsUnit.Pixel))using(var brush=new SolidBrush(color))g.DrawString(text,font,brush,x,y);}
 public static int Main(string[] args){try{
  if(args.Length!=3||File.Exists(args[2]))throw new IOException("Pass production mock samples JSON, unchanged approved icon, and a NEW output PNG.");
  var root=(Dictionary<string,object>)new JavaScriptSerializer().DeserializeObject(File.ReadAllText(args[0]));if(!(bool)root["mock"])throw new IOException("Only an explicitly labelled mock fixture may be rendered.");
  var list=(object[])root["samples"];var sample=(Dictionary<string,object>)list[1];var activity=(Dictionary<string,object>)sample["activity"];
  using(var image=new Bitmap(480,292))using(var g=Graphics.FromImage(image))using(var icon=Image.FromFile(args[1])){
   g.Clear(C("#16161b"));g.SmoothingMode=SmoothingMode.AntiAlias;Round(g,new Rectangle(1,1,478,290),15,C("#292930"));
   Text(g,"MOCK · NOT A CAPTURED DISCORD CARD",12,FontStyle.Bold,C("#f5c97d"),20,17);
   g.DrawImageUnscaled(icon,20,62);
   Text(g,(string)activity["name"],22,FontStyle.Bold,C("#f2f2f7"),116,57);
   Text(g,(string)activity["details"],14,FontStyle.Regular,C("#d9d9e3"),116,91);
   Text(g,activity.ContainsKey("state")?(string)activity["state"]:"",16,FontStyle.Regular,C("#eeeef4"),116,119);
   Text(g,"21:36 elapsed · simulated session",12,FontStyle.Regular,C("#b6b6c6"),116,149);
   Round(g,new Rectangle(20,192,438,37),6,C("#41414d"));using(var font=new Font("Segoe UI",14,FontStyle.Bold,GraphicsUnit.Pixel))using(var brush=new SolidBrush(Color.White))using(var format=new StringFormat{Alignment=StringAlignment.Center,LineAlignment=StringAlignment.Center})g.DrawString("About Co-op+",font,brush,new RectangleF(20,192,438,37),format);
   Text(g,"Production formatter payload · synthetic game state",12,FontStyle.Regular,C("#aaaabb"),20,245);
   Text(g,"Actual Discord layout and visibility remain unverified.",11,FontStyle.Regular,C("#aaaabb"),20,265);
   image.Save(args[2],ImageFormat.Png);
  }Console.WriteLine("Saved labelled code-rendered mock only.");return 0;
 }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}}
}
