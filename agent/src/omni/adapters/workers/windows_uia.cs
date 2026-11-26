// Isolated MTA UIA worker. Semantic writes require a host-validated approval.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Automation;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Security.Cryptography;
using System.Numerics;
using System.Globalization;

namespace OmniV2Desktop {
    public sealed class Observation {
        public readonly List<Dictionary<string, object>> Nodes = new List<Dictionary<string, object>>();
        public bool Truncated;
        internal int TextBytes;
    }
    internal sealed class Entry {
        internal AutomationElement Element;
        internal int Parent, Depth;
        internal Entry(AutomationElement element, int parent, int depth) { Element=element; Parent=parent; Depth=depth; }
    }
    public static partial class Observer {
        [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO { public uint Size, Tick; }
        [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO info);
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        static uint InputTick() {
            var info=new LASTINPUTINFO { Size=(uint)Marshal.SizeOf(typeof(LASTINPUTINFO)) };
            if (!GetLastInputInfo(ref info)) throw Denied();
            return info.Tick;
        }
        static string Digest(string value) {
            using (var sha=SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value??""))).Replace("-","").ToLowerInvariant();
        }
        static string[] Actions(AutomationElement element) {
            var state=element.Current; var actions=new List<string>(); object pattern;
            if (state.IsPassword || !state.IsEnabled || state.IsOffscreen) return actions.ToArray();
            if (element.TryGetCurrentPattern(InvokePattern.Pattern,out pattern)) actions.Add("invoke");
            if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pattern)) actions.Add("select");
            if (element.TryGetCurrentPattern(ValuePattern.Pattern,out pattern) && !((ValuePattern)pattern).Current.IsReadOnly)
                actions.Add("set_text");
            if (state.IsKeyboardFocusable) {
                actions.Add("press_key");
                if (state.ControlType==ControlType.Edit) actions.Add("type_text");
            }
            actions.Add("click");
            return actions.ToArray();
        }
        static Dictionary<string,object> Receipt(string status, string effect, string code, string verification) {
            return new Dictionary<string,object> {{"status",status},{"effect",effect},{"error_code",code},{"verification",verification}};
        }
        static AutomationElement Find(AutomationElement root, int[] runtime, uint pid) {
            var queue=new Queue<Entry>(); queue.Enqueue(new Entry(root,0,1)); int count=0;
            var walker=TreeWalker.ControlViewWalker;
            while (queue.Count>0 && count<128) {
                var item=queue.Dequeue(); count++; var state=item.Element.Current;
                if (state.ProcessId!=pid || state.IsPassword) continue;
                if (runtime.SequenceEqual(item.Element.GetRuntimeId())) return item.Element;
                if (item.Depth>=8) continue;
                var child=walker.GetFirstChild(item.Element);
                while (child!=null && queue.Count+count<128) {
                    queue.Enqueue(new Entry(child,0,item.Depth+1)); child=walker.GetNextSibling(child);
                }
            }
            throw Changed();
        }
        static void NodeIdentity(AutomationElement root, AutomationElement node, uint pid, int[] rootId, int[] runtime, string role, string digest) {
            if (!rootId.SequenceEqual(root.GetRuntimeId()) || !runtime.SequenceEqual(node.GetRuntimeId())) throw Changed();
            var state=node.Current;
            if (state.ProcessId!=pid || state.IsPassword || !state.IsEnabled || state.IsOffscreen) throw Denied();
            if (state.ControlType.ProgrammaticName!=role || Digest(state.Name)!=digest) throw Changed();
        }
        public static Dictionary<string,object> Act(long hwnd, uint pid, string created, string application, string session,
                                                     int[] rootId, int[] runtime, string role, string digest, string kind, string text) {
            bool dispatched=false; IntPtr handle=IntPtr.Zero, window=new IntPtr(hwnd);
            try {
                if (Thread.CurrentThread.GetApartmentState()!=ApartmentState.MTA) throw Denied();
                if (rootId==null || runtime==null || rootId.Length<1 || rootId.Length>32 || runtime.Length<1 || runtime.Length>32 ||
                    (kind!="invoke" && kind!="select" && kind!="set_text") || text==null || text.Length>1024 || text.Any(Char.IsControl) ||
                    (kind!="set_text" && text!="")) throw Denied();
                uint tick=InputTick(); IntPtr foreground=GetForegroundWindow();
                handle=OpenProcess(0x1000|0x100000,false,pid); if (handle==IntPtr.Zero) throw Denied();
                Identity(handle,window,pid,created,application,session);
                var root=AutomationElement.FromHandle(window);
                if (root==null || root.Current.ProcessId!=pid || root.Current.NativeWindowHandle!=hwnd) throw Changed();
                var node=Find(root,runtime,pid);
                NodeIdentity(root,node,pid,rootId,runtime,role,digest);
                object pattern;
                var patternId=kind=="invoke" ? InvokePattern.Pattern : kind=="select" ? SelectionItemPattern.Pattern : ValuePattern.Pattern;
                if (!node.TryGetCurrentPattern(patternId,out pattern) || (kind=="set_text" && ((ValuePattern)pattern).Current.IsReadOnly))
                    throw new InvalidOperationException("desktop_pattern_unavailable");
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if (InputTick()!=tick || GetForegroundWindow()!=foreground) throw new InvalidOperationException("desktop_user_intervened");
                dispatched=true;
                if (kind=="invoke") ((InvokePattern)pattern).Invoke();
                else if (kind=="select") ((SelectionItemPattern)pattern).Select();
                else ((ValuePattern)pattern).SetValue(text);
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if (InputTick()!=tick || GetForegroundWindow()!=foreground) throw new InvalidOperationException("desktop_user_intervened");
                bool selected=kind=="select" && ((SelectionItemPattern)pattern).Current.IsSelected;
                object reader;
                bool matches=kind=="set_text" && node.TryGetCurrentPattern(TextPattern.Pattern,out reader) &&
                    ((TextPattern)reader).DocumentRange.GetText(text.Length+1)==text;
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if (InputTick()!=tick || GetForegroundWindow()!=foreground) throw new InvalidOperationException("desktop_user_intervened");
                if (selected) return Receipt("ok","applied","","selected");
                if (matches) return Receipt("ok","applied","","value_matches");
                // A returned Invoke call proves dispatch only, not the target application's business outcome.
                return Receipt("unknown","unknown","desktop_postcondition_unverified","not_verified");
            } catch (Exception error) {
                string code="desktop_action_failed";
                if (new [] {"desktop_target_changed","desktop_permission_denied","desktop_pattern_unavailable","desktop_user_intervened"}.Contains(error.Message)) code=error.Message;
                return Receipt(dispatched?"unknown":"denied",dispatched?"unknown":"none",code,dispatched?"not_verified":"not_dispatched");
            } finally { if (handle!=IntPtr.Zero) CloseHandle(handle); }
        }
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out RECT bounds);
        [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
        [DllImport("user32.dll")] static extern bool GetWindowDisplayAffinity(IntPtr window, out uint affinity);
        [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window, uint attribute, out int value, uint size);
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        [DllImport("kernel32.dll")] static extern bool ProcessIdToSessionId(uint pid, out uint session);
        [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr p, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern bool QueryFullProcessImageName(IntPtr p, uint flags, StringBuilder path, ref uint length);
        [DllImport("advapi32.dll")] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
        [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
        [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
        [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h, int index, IntPtr buffer, uint size, out uint needed);
        [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode)] static extern bool WTSQuerySessionInformation(IntPtr server, uint session, int kind, out IntPtr buffer, out uint length);
        [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);

        static Exception Changed() { return new InvalidOperationException("desktop_target_changed"); }
        static Exception Denied() { return new InvalidOperationException("desktop_permission_denied"); }
        static void TraceTestPhase(string phase) {
            if (Environment.GetEnvironmentVariable("OMNI_TEST_OFFLINE")=="1") Console.Error.WriteLine("omni-uia:"+phase);
        }
        static string Name(IntPtr handle) {
            IntPtr data = Marshal.AllocHGlobal(512);
            try {
                uint needed;
                if (handle==IntPtr.Zero || !GetUserObjectInformation(handle, 2, data, 512, out needed) || needed>512) throw Denied();
                return Marshal.PtrToStringUni(data);
            } finally { Marshal.FreeHGlobal(data); }
        }
        static int Flag(IntPtr handle, int index, uint length, int offset) {
            IntPtr data=Marshal.AllocHGlobal((int)length);
            try {
                uint needed;
                if (!GetUserObjectInformation(handle,index,data,length,out needed) || needed>length) throw Denied();
                return Marshal.ReadInt32(data,offset);
            } finally { Marshal.FreeHGlobal(data); }
        }
        static string Session(uint pid, uint thread) {
            uint session, own;
            if (!ProcessIdToSessionId(pid,out session) || !ProcessIdToSessionId(GetCurrentProcessId(),out own) || own==0 || session!=own) throw Denied();
            IntPtr buffer; uint length;
            if (!WTSQuerySessionInformation(IntPtr.Zero,own,8,out buffer,out length)) throw Denied();
            try { if (length!=4 || Marshal.ReadInt32(buffer)!=0) throw Denied(); }
            finally { WTSFreeMemory(buffer); }
            IntPtr station=GetProcessWindowStation(), desktop=GetThreadDesktop(GetCurrentThreadId());
            if ((Flag(station,1,12,8)&1)==0 || Flag(desktop,6,4,0)==0) throw Denied();
            IntPtr input=OpenInputDesktop(0,false,1);
            if (input==IntPtr.Zero) throw Denied();
            try {
                string name=Name(input);
                if (name!=Name(desktop) || name!=Name(GetThreadDesktop(thread))) throw Denied();
                return "windows:"+own+":"+Name(station)+":"+name;
            } finally { CloseDesktop(input); }
        }
        public static string CreationBits(long filetime) {
            // Round the exact FILETIME ratio to IEEE754 once, ties to even. Framework's
            // decimal-to-double conversion can differ by one ULP from Python integer division.
            BigInteger numerator=new BigInteger(filetime)-116444736000000000L;
            if (numerator<=0) throw Changed();
            BigInteger denominator=10000000; int exponent=0;
            while (numerator>=denominator*2) { denominator*=2; exponent++; }
            while (numerator<denominator) { numerator*=2; exponent--; }
            BigInteger remainder;
            BigInteger mantissa=BigInteger.DivRem(numerator<<52,denominator,out remainder);
            if (remainder*2>denominator || (remainder*2==denominator && !mantissa.IsEven)) mantissa++;
            if (mantissa==(BigInteger.One<<53)) { mantissa>>=1; exponent++; }
            ulong bits=((ulong)(exponent+1023)<<52)|((ulong)mantissa&0xFFFFFFFFFFFFFUL);
            return "ieee754:"+bits.ToString("x16",CultureInfo.InvariantCulture);
        }
        static void Identity(IntPtr handle, IntPtr window, uint expectedPid, string created, string application, string session) {
            uint pid; uint thread=GetWindowThreadProcessId(window,out pid);
            if (thread==0 || pid!=expectedPid || !IsWindowVisible(window) || WaitForSingleObject(handle,0)!=258) throw Changed();
            TraceTestPhase("window");
            if (Session(pid,thread)!=session) throw Denied();
            TraceTestPhase("session");
            long c,e,k,u;
            if (!GetProcessTimes(handle,out c,out e,out k,out u) || CreationBits(c)!=created) throw Changed();
            var path=new StringBuilder(32768); uint size=32768;
            if (!QueryFullProcessImageName(handle,0,path,ref size) || !String.Equals(path.ToString(),application,StringComparison.OrdinalIgnoreCase)) throw Changed();
            IntPtr token;
            if (!OpenProcessToken(handle,8,out token)) throw Denied();
            try {
                using (var target=new WindowsIdentity(token))
                using (var current=WindowsIdentity.GetCurrent()) {
                    if (target.User==null || current.User==null || !target.User.Equals(current.User)) throw Denied();
                }
            } finally { CloseHandle(token); }
            TraceTestPhase("identity");
        }
        static RECT CaptureBounds(IntPtr window) {
            RECT bounds; int cloaked; uint affinity;
            if (IsIconic(window) || !GetWindowRect(window,out bounds) ||
                (DwmGetWindowAttribute(window,14,out cloaked,4)==0 && cloaked!=0))
                throw new InvalidOperationException("desktop_capture_unavailable");
            if (GetWindowDisplayAffinity(window,out affinity) && affinity!=0)
                throw new InvalidOperationException("desktop_capture_protected");
            long width=(long)bounds.Right-bounds.Left, height=(long)bounds.Bottom-bounds.Top;
            if (width<=0 || height<=0 || width>8192 || height>8192 || width*height>16000000)
                throw new InvalidOperationException("desktop_capture_too_large");
            return bounds;
        }
        public static Dictionary<string,object> Capture(long hwnd, uint pid, string created, string application, string session, string destination) {
            IntPtr window=new IntPtr(hwnd), handle=OpenProcess(0x1000|0x100000,false,pid);
            if (handle==IntPtr.Zero) throw Denied();
            IntPtr dpi=IntPtr.Zero;
            try {
                dpi=SetThreadDpiAwarenessContext(new IntPtr(-4));
                if (dpi==IntPtr.Zero) throw new InvalidOperationException("desktop_capture_unavailable");
                Identity(handle,window,pid,created,application,session);
                RECT before=CaptureBounds(window);
                int width=before.Right-before.Left, height=before.Bottom-before.Top;
                using (var bitmap=new Bitmap(width,height,PixelFormat.Format24bppRgb)) {
                    using (var graphics=Graphics.FromImage(bitmap)) {
                        graphics.Clear(Color.Black);
                        IntPtr dc=graphics.GetHdc();
                        try {
                            // May block in an unresponsive target. The host owns the total deadline and Job.
                            if (!PrintWindow(window,dc,0)) throw new InvalidOperationException("desktop_capture_failed");
                            TraceTestPhase("printed");
                        } finally { graphics.ReleaseHdc(dc); }
                    }
                    Identity(handle,window,pid,created,application,session);
                    RECT after=CaptureBounds(window);
                    if (!before.Equals(after)) throw Changed();
                    if (!Path.IsPathRooted(destination) || Path.GetFileName(destination)!="frame.png") throw Denied();
                    // Host-selected empty private directory; no overwrite, no screen-wide fallback.
                    using (var file=new FileStream(destination,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None,65536,FileOptions.WriteThrough)) {
                        bitmap.Save(file,ImageFormat.Png);
                        if (file.Length>16777216) throw new InvalidOperationException("desktop_capture_too_large");
                        file.Flush(true); file.Position=0;
                        string digest;
                        using (var sha=SHA256.Create()) digest=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant();
                        Identity(handle,window,pid,created,application,session);
                        if (!after.Equals(CaptureBounds(window))) throw Changed();
                        return new Dictionary<string,object> {{"width",width},{"height",height},{"bytes",file.Length},{"sha256",digest},{"method","print_window"}};
                    }
                }
            } catch (UnauthorizedAccessException) { throw Denied(); }
            finally { if (dpi!=IntPtr.Zero) SetThreadDpiAwarenessContext(dpi); CloseHandle(handle); }
        }
        static string Text(string raw, Observation result) {
            if (String.IsNullOrEmpty(raw)) return "";
            var output=new StringBuilder(); int characters=0, position=0;
            while (position<raw.Length && characters<512) {
                char c=raw[position++]; string piece;
                if (Char.IsHighSurrogate(c)) {
                    if (position>=raw.Length || !Char.IsLowSurrogate(raw[position])) continue;
                    piece=new string(new [] {c,raw[position++]});
                } else {
                    if (Char.IsLowSurrogate(c) || (Char.IsControl(c) && c!='\n' && c!='\r' && c!='\t')) continue;
                    piece=c.ToString();
                }
                int bytes=Encoding.UTF8.GetByteCount(piece);
                if (result.TextBytes+bytes>16384) { result.Truncated=true; break; }
                result.TextBytes+=bytes; output.Append(piece); characters++;
            }
            if (position<raw.Length) result.Truncated=true;
            return output.ToString();
        }
        public static Observation Observe(long hwnd, uint pid, string created, string application, string session) {
            if (Thread.CurrentThread.GetApartmentState()!=ApartmentState.MTA) throw new InvalidOperationException("desktop_observation_unavailable");
            IntPtr window=new IntPtr(hwnd), handle=OpenProcess(0x1000|0x100000,false,pid);
            if (handle==IntPtr.Zero) throw Denied();
            try {
                Identity(handle,window,pid,created,application,session);
                var root=AutomationElement.FromHandle(window);
                TraceTestPhase("uia-root");
                if (root==null || root.Current.ProcessId!=pid || root.Current.NativeWindowHandle!=hwnd) throw Changed();
                int[] rootId=root.GetRuntimeId();
                var result=new Observation(); var queue=new Queue<Entry>();
                queue.Enqueue(new Entry(root,0,1));
                var walker=TreeWalker.ControlViewWalker;
                while (queue.Count>0 && result.Nodes.Count<128) {
                    var item=queue.Dequeue(); var element=item.Element;
                    var state=element.Current;
                    if (state.ProcessId!=pid) { result.Truncated=true; continue; }
                    bool password=state.IsPassword; string name="", text="";
                    if (!password && result.TextBytes<16384) {
                        name=Text(state.Name,result);
                        object pattern;
                        // TextPattern has a bounded read; ValuePattern does not, so it is not fetched.
                        if (result.TextBytes<16384 && element.TryGetCurrentPattern(TextPattern.Pattern,out pattern))
                            text=Text(((TextPattern)pattern).DocumentRange.GetText(513),result);
                        if (element.Current.IsPassword) { password=true; name=""; text=""; }
                    } else if (!password) result.Truncated=true;
                    var rectangle=state.BoundingRectangle;
                    double[] bounds=rectangle.IsEmpty ? new double[] {0,0,0,0} : new double[] {rectangle.X,rectangle.Y,rectangle.Width,rectangle.Height};
                    if (bounds.Any(v=>Double.IsNaN(v)||Double.IsInfinity(v)||Math.Abs(v)>10000000)) bounds=new double[] {0,0,0,0};
                    int id=result.Nodes.Count+1;
                    var actions=password ? new string[0] : Actions(element);
                    int[] runtime=actions.Length>0 ? element.GetRuntimeId() : null;
                    if (runtime!=null && (runtime.Length<1 || runtime.Length>32 || rootId.Length<1 || rootId.Length>32)) actions=new string[0];
                    object reference=actions.Length==0 ? null : new Dictionary<string,object> {
                        {"root",rootId},{"runtime",runtime},{"role",state.ControlType.ProgrammaticName},{"name_sha256",Digest(state.Name)}
                    };
                    result.Nodes.Add(new Dictionary<string,object> {
                        {"id",id},{"parent",item.Parent==0 ? null : (object)item.Parent},
                        {"role",state.ControlType.ProgrammaticName},{"name",name},{"text",text},
                        {"password",password},{"enabled",state.IsEnabled},{"offscreen",state.IsOffscreen},{"bounds",bounds},
                        {"reference",reference},{"actions",actions}
                    });
                    if (password) continue;
                    var child=walker.GetFirstChild(element);
                    if (item.Depth>=8) { if (child!=null) result.Truncated=true; continue; }
                    while (child!=null) {
                        if (queue.Count+result.Nodes.Count>=128) { result.Truncated=true; break; }
                        queue.Enqueue(new Entry(child,id,item.Depth+1));
                        child=walker.GetNextSibling(child);
                    }
                }
                if (queue.Count>0) result.Truncated=true;
                Identity(handle,window,pid,created,application,session);
                var fresh=AutomationElement.FromHandle(window);
                if (fresh==null || !rootId.SequenceEqual(fresh.GetRuntimeId()) || !Automation.Compare(root,fresh)) throw Changed();
                return result;
            } catch (ElementNotAvailableException) { throw Changed(); }
              catch (UnauthorizedAccessException) { throw Denied(); }
            finally { CloseHandle(handle); }
        }
    }
}
