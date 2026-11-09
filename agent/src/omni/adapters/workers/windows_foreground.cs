// One approved input batch. No focus stealing, key logging, clipboard or persistent hooks.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Automation.Text;

namespace OmniV2Desktop {
    public static partial class Observer {
        [StructLayout(LayoutKind.Sequential)] struct InputPoint { public int X,Y; }
        [StructLayout(LayoutKind.Sequential)] public struct MouseInput { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] public struct KeyInput { public ushort Key,Scan; public uint Flags,Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Explicit)] public struct InputUnion { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyInput Key; }
        [StructLayout(LayoutKind.Sequential)] public struct NativeInput { public uint Type; public InputUnion Value; }
        [StructLayout(LayoutKind.Sequential)] struct KeyNotice { public uint Key,Scan,Flags,Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct MouseNotice { public InputPoint Point; public uint Data,Flags,Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct InputMessage { public IntPtr Window; public uint Message; public UIntPtr WParam; public IntPtr LParam; public uint Time; public InputPoint Point; public uint Private; }
        delegate IntPtr InputHook(int code, IntPtr message, IntPtr data);
        delegate void FocusHook(IntPtr hook, uint kind, IntPtr window, int obj, int child, uint thread, uint time);
        [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint count, NativeInput[] input, int size);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(InputPoint point);
        [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr window, uint flag);
        [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SetWindowsHookEx(int kind, InputHook callback, IntPtr module, uint thread);
        [DllImport("user32.dll")] static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr message, IntPtr data);
        [DllImport("user32.dll")] static extern bool UnhookWindowsHookEx(IntPtr hook);
        [DllImport("user32.dll")] static extern IntPtr SetWinEventHook(uint first, uint last, IntPtr module, FocusHook callback, uint process, uint thread, uint flags);
        [DllImport("user32.dll")] static extern bool UnhookWinEvent(IntPtr hook);
        [DllImport("user32.dll")] static extern int GetMessage(out InputMessage message, IntPtr window, uint first, uint last);
        [DllImport("user32.dll")] static extern bool TranslateMessage(ref InputMessage message);
        [DllImport("user32.dll")] static extern IntPtr DispatchMessage(ref InputMessage message);
        [DllImport("user32.dll")] static extern bool PostThreadMessage(uint thread, uint message, UIntPtr first, IntPtr second);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandle(string name);

        // Native input port is selected by the fixed helper, never by an IPC field.
        public interface IForegroundInput : IDisposable {
            bool HasFailed(); bool HasIntervened(); int ObservedEvents(); UIntPtr MarkerValue();
            void Arm(); uint Tick(); bool Released(); bool Focused(IntPtr window, AutomationElement node, string kind);
            System.Windows.Point ClickPoint(AutomationElement node, IntPtr window);
            uint Send(NativeInput[] input);
        }
        sealed class InputMonitor : IForegroundInput {
            readonly Thread thread; readonly ManualResetEvent started=new ManualResetEvent(false);
            readonly InputHook keyboard,mouse; readonly FocusHook focus;
            readonly List<GCHandle> roots=new List<GCHandle>();
            internal readonly UIntPtr Marker;
            internal volatile bool Armed,Intervened,Failed;
            internal int OwnEvents; uint threadId; bool disposed;
            internal InputMonitor() {
                Marker=new UIntPtr(BitConverter.ToUInt64(Guid.NewGuid().ToByteArray(),0));
                keyboard=(code,message,data)=> {
                    if (code>=0) {
                        var info=(KeyNotice)Marshal.PtrToStructure(data,typeof(KeyNotice));
                        Notice((info.Flags&0x10)!=0,info.Extra);
                    }
                    return CallNextHookEx(IntPtr.Zero,code,message,data);
                };
                mouse=(code,message,data)=> {
                    if (code>=0) {
                        var info=(MouseNotice)Marshal.PtrToStructure(data,typeof(MouseNotice));
                        Notice((info.Flags&1)!=0,info.Extra);
                    }
                    return CallNextHookEx(IntPtr.Zero,code,message,data);
                };
                focus=(hook,kind,window,obj,child,id,time)=> { if (Armed) Intervened=true; };
                roots.Add(GCHandle.Alloc(keyboard)); roots.Add(GCHandle.Alloc(mouse)); roots.Add(GCHandle.Alloc(focus));
                thread=new Thread(Pump); thread.IsBackground=true; thread.SetApartmentState(ApartmentState.MTA); thread.Start();
                if (!started.WaitOne(1000) || Failed) { Dispose(); throw Denied(); }
            }
            void Notice(bool injected, UIntPtr extra) {
                if (!Armed) return;
                if (injected && extra==Marker) Interlocked.Increment(ref OwnEvents);
                else Intervened=true;
            }
            public bool HasFailed() { return Failed; }
            public bool HasIntervened() { return Intervened; }
            public int ObservedEvents() { return OwnEvents; }
            public UIntPtr MarkerValue() { return Marker; }
            public void Arm() { Armed=true; }
            public uint Tick() { return InputTick(); }
            public bool Released() { return KeysReleased(); }
            public bool Focused(IntPtr window, AutomationElement node, string kind) { return FocusMatches(window,node,kind); }
            public System.Windows.Point ClickPoint(AutomationElement node, IntPtr window) {
                System.Windows.Point point;
                if (!node.TryGetClickablePoint(out point) || Double.IsNaN(point.X) || Double.IsNaN(point.Y)) throw Denied();
                var hit=AutomationElement.FromPoint(point);
                if (GetAncestor(WindowFromPoint(new InputPoint {X=checked((int)point.X),Y=checked((int)point.Y)}),2)!=window ||
                    hit==null || !Automation.Compare(hit,node)) throw Changed();
                return point;
            }
            public uint Send(NativeInput[] input) { return SendInput((uint)input.Length,input,Marshal.SizeOf(typeof(NativeInput))); }
            void Pump() {
                IntPtr keys=IntPtr.Zero,pointer=IntPtr.Zero,foreground=IntPtr.Zero,focused=IntPtr.Zero;
                try {
                    threadId=GetCurrentThreadId(); IntPtr module=GetModuleHandle(null);
                    keys=SetWindowsHookEx(13,keyboard,module,0); pointer=SetWindowsHookEx(14,mouse,module,0);
                    foreground=SetWinEventHook(3,3,IntPtr.Zero,focus,0,0,0);
                    focused=SetWinEventHook(0x8005,0x8005,IntPtr.Zero,focus,0,0,0);
                    if (keys==IntPtr.Zero || pointer==IntPtr.Zero || foreground==IntPtr.Zero || focused==IntPtr.Zero) { Failed=true; return; }
                    started.Set(); InputMessage message; int result;
                    while ((result=GetMessage(out message,IntPtr.Zero,0,0))>0) { TranslateMessage(ref message); DispatchMessage(ref message); }
                    if (result<0) Failed=true;
                } catch { Failed=true; }
                finally {
                    if (keys!=IntPtr.Zero && !UnhookWindowsHookEx(keys)) Failed=true;
                    if (pointer!=IntPtr.Zero && !UnhookWindowsHookEx(pointer)) Failed=true;
                    if (foreground!=IntPtr.Zero && !UnhookWinEvent(foreground)) Failed=true;
                    if (focused!=IntPtr.Zero && !UnhookWinEvent(focused)) Failed=true;
                    started.Set();
                }
            }
            public void Dispose() {
                if (disposed) return; disposed=true;
                if (thread.IsAlive) { PostThreadMessage(threadId,0x12,UIntPtr.Zero,IntPtr.Zero); if (!thread.Join(500)) { Failed=true; return; } }
                foreach (var root in roots) root.Free(); started.Dispose();
            }
        }
        static bool KeysReleased() {
            for (int key=1;key<256;key++) if ((GetAsyncKeyState(key)&0x8000)!=0) return false;
            return true;
        }
        static bool FocusMatches(IntPtr window, AutomationElement node, string kind) {
            if (GetForegroundWindow()!=window) return false;
            if (kind=="click") return true;
            var focused=AutomationElement.FocusedElement;
            return focused!=null && Automation.Compare(node,focused) && node.GetRuntimeId().SequenceEqual(focused.GetRuntimeId());
        }
        static NativeInput KeyStroke(ushort key, ushort scan, uint flags, UIntPtr marker) {
            return new NativeInput {Type=1,Value=new InputUnion {Key=new KeyInput {Key=key,Scan=scan,Flags=flags,Extra=marker}}};
        }
        static ushort KeyCode(string name) {
            switch (name) {
                case "Enter":return 13; case "Tab":return 9; case "Escape":return 27; case "Backspace":return 8;
                case "Delete":return 46; case "Home":return 36; case "End":return 35; case "Left":return 37;
                case "Right":return 39; case "Up":return 38; case "Down":return 40;
                default:throw Denied();
            }
        }
        static NativeInput[] Inputs(AutomationElement node, IntPtr window, string kind, string text, IForegroundInput monitor) {
            UIntPtr marker=monitor.MarkerValue();
            var result=new List<NativeInput>();
            if (kind=="type_text") {
                if (String.IsNullOrEmpty(text) || text.Length>256 || text.Any(Char.IsControl)) throw Denied();
                for (int i=0;i<text.Length;i++) { result.Add(KeyStroke(0,text[i],4,marker)); result.Add(KeyStroke(0,text[i],6,marker)); }
            } else if (kind=="press_key") {
                ushort key=KeyCode(text); uint extended=key>=35 && key<=46 ? 1U : 0U;
                result.Add(KeyStroke(key,0,extended,marker)); result.Add(KeyStroke(key,0,extended|2,marker));
            } else if (kind=="click") {
                if (text!="") throw Denied();
                System.Windows.Point point=monitor.ClickPoint(node,window);
                var native=new InputPoint {X=checked((int)point.X),Y=checked((int)point.Y)};
                if (!node.Current.BoundingRectangle.Contains(point)) throw Changed();
                int left=GetSystemMetrics(76),top=GetSystemMetrics(77),width=GetSystemMetrics(78),height=GetSystemMetrics(79);
                if (width<2 || height<2 || native.X<left || native.Y<top || native.X>=left+width || native.Y>=top+height) throw Denied();
                int x=(int)(((long)native.X-left)*65535/(width-1)), y=(int)(((long)native.Y-top)*65535/(height-1));
                result.Add(new NativeInput {Type=0,Value=new InputUnion {Mouse=new MouseInput {X=x,Y=y,Flags=0xC001,Extra=marker}}});
                result.Add(new NativeInput {Type=0,Value=new InputUnion {Mouse=new MouseInput {Flags=2,Extra=marker}}});
                result.Add(new NativeInput {Type=0,Value=new InputUnion {Mouse=new MouseInput {Flags=4,Extra=marker}}});
            } else throw Denied();
            return result.ToArray();
        }
        static string AppendBefore(AutomationElement node) {
            object pattern;
            if (!node.TryGetCurrentPattern(TextPattern.Pattern,out pattern)) return null;
            var text=(TextPattern)pattern; var selected=text.GetSelection(); var end=text.DocumentRange;
            string value=end.GetText(513);
            if (value.Length>512 || selected.Length!=1 ||
                selected[0].CompareEndpoints(TextPatternRangeEndpoint.Start,selected[0],TextPatternRangeEndpoint.End)!=0 ||
                selected[0].CompareEndpoints(TextPatternRangeEndpoint.End,end,TextPatternRangeEndpoint.End)!=0) return null;
            return value;
        }
        public static Dictionary<string,object> Foreground(long hwnd, uint pid, string created, string application, string session,
            int[] rootId, int[] runtime, string role, string digest, string kind, string text, Func<bool> permit) {
            return ForegroundWithInput(hwnd,pid,created,application,session,rootId,runtime,role,digest,kind,text,permit,null);
        }
        public static Dictionary<string,object> ForegroundWithInput(long hwnd, uint pid, string created, string application, string session,
            int[] rootId, int[] runtime, string role, string digest, string kind, string text, Func<bool> permit, IForegroundInput inputPort) {
            IntPtr handle=IntPtr.Zero, window=new IntPtr(hwnd); bool dispatched=false; IForegroundInput monitor=null;
            Dictionary<string,object> result;
            try {
                if (Thread.CurrentThread.GetApartmentState()!=ApartmentState.MTA || rootId==null || runtime==null ||
                    rootId.Length<1 || rootId.Length>32 || runtime.Length<1 || runtime.Length>32) throw Denied();
                handle=OpenProcess(0x1000|0x100000,false,pid); if (handle==IntPtr.Zero) throw Denied();
                Identity(handle,window,pid,created,application,session);
                var root=AutomationElement.FromHandle(window); var node=Find(root,runtime,pid);
                NodeIdentity(root,node,pid,rootId,runtime,role,digest);
                monitor=inputPort ?? new InputMonitor(); var watch=Stopwatch.StartNew(); uint tick=monitor.Tick(); long quiet=watch.ElapsedMilliseconds;
                while (true) {
                    Identity(handle,window,pid,created,application,session);
                    uint current=monitor.Tick();
                    if (current!=tick || !monitor.Focused(window,node,kind) || !monitor.Released()) { tick=current;quiet=watch.ElapsedMilliseconds; }
                    else if (watch.ElapsedMilliseconds-quiet>=250) break;
                    if (watch.ElapsedMilliseconds>=10000 || monitor.HasFailed()) throw new InvalidOperationException("desktop_focus_timeout");
                    Thread.Sleep(10);
                }
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if ((kind=="type_text" && node.Current.ControlType!=ControlType.Edit) ||
                    (kind!="click" && !node.Current.IsKeyboardFocusable)) throw Denied();
                monitor.Arm();
                // Host receives only a bound readiness frame and rechecks revocation before permitting input.
                if (!permit()) throw new InvalidOperationException("desktop_action_revoked");
                string before=kind=="type_text" ? AppendBefore(node) : null;
                NativeInput[] input=Inputs(node,window,kind,text,monitor);
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if (monitor.HasFailed() || monitor.HasIntervened() || monitor.Tick()!=tick || !monitor.Focused(window,node,kind) || !monitor.Released())
                    throw new InvalidOperationException("desktop_user_intervened");
                dispatched=true;
                uint accepted=monitor.Send(input);
                if (accepted<input.Length) {
                    // A partial batch may end after our own down event. Release only
                    // that accepted event; never repeat a character or a click.
                    if (kind!="click" && accepted%2==1) {
                        var release=input[accepted-1]; release.Value.Key.Flags|=2;
                        monitor.Send(new [] {release});
                    } else if (kind=="click" && accepted==2) {
                        monitor.Send(new [] {input[2]});
                    }
                }
                var settle=Stopwatch.StartNew();
                while (monitor.ObservedEvents()<input.Length && settle.ElapsedMilliseconds<200 && !monitor.HasIntervened() && !monitor.HasFailed()) Thread.Sleep(2);
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                bool verified=false; object reader;
                if (kind=="type_text" && before!=null && before.Length+text.Length<=512 && node.TryGetCurrentPattern(TextPattern.Pattern,out reader))
                    verified=((TextPattern)reader).DocumentRange.GetText(before.Length+text.Length+1)==before+text;
                Identity(handle,window,pid,created,application,session);
                NodeIdentity(AutomationElement.FromHandle(window),node,pid,rootId,runtime,role,digest);
                if (accepted!=input.Length || monitor.ObservedEvents()<input.Length || monitor.HasFailed() || monitor.HasIntervened() || !monitor.Focused(window,node,kind))
                    throw new InvalidOperationException("desktop_input_unverified");
                result=verified ? Receipt("ok","applied","","text_appended") : Receipt("unknown","unknown","desktop_postcondition_unverified","not_verified");
            } catch (Exception error) {
                string code="desktop_foreground_failed";
                if (new [] {"desktop_target_changed","desktop_permission_denied","desktop_focus_timeout","desktop_action_revoked","desktop_user_intervened","desktop_input_unverified"}.Contains(error.Message)) code=error.Message;
                result=Receipt(dispatched?"unknown":"denied",dispatched?"unknown":"none",code,dispatched?"not_verified":"not_dispatched");
            } finally {
                if (handle!=IntPtr.Zero) CloseHandle(handle);
            }
            if (monitor!=null) {
                monitor.Dispose();
                if (monitor.HasFailed()) result=Receipt(dispatched?"unknown":"denied",dispatched?"unknown":"none","desktop_monitor_cleanup_failed",dispatched?"not_verified":"not_dispatched");
            }
            return result;
        }
    }
}
