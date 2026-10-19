#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::{collections::HashMap, io::{BufRead, BufReader, Write}, process::{Child, ChildStdin, Command, Stdio}, sync::{Arc, Mutex, atomic::{AtomicU64, Ordering}, mpsc}, time::Duration};
use tauri::{Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

type Waiters = Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>>;
struct Engine {
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    pending: Waiters,
    sequence: AtomicU64,
}

impl Engine {
    fn spawn(app: &tauri::AppHandle) -> Result<Self, String> {
        let state_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
        std::fs::create_dir_all(&state_dir).map_err(|e| e.to_string())?;
        #[cfg(debug_assertions)]
        let mut command = {
            // Only developer builds accept an interpreter override. Release uses
            // the signed/bundled sidecar next to the desktop executable.
            if let Some(python) = std::env::var_os("HOLOQA_DESKTOP_PYTHON") {
                let mut cmd = Command::new(python);
                cmd.args(["-m", "holoqa.desktop"]);
                cmd
            } else {
                let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("binaries").join(if cfg!(windows) {"holoqa-desktop-engine-x86_64-pc-windows-msvc.exe"} else {"holoqa-desktop-engine"});
                Command::new(path)
            }
        };
        #[cfg(not(debug_assertions))]
        let mut command = {
            let directory = std::env::current_exe().map_err(|e| e.to_string())?
                .parent().ok_or("Cannot locate application directory")?.to_path_buf();
            Command::new(directory.join(if cfg!(windows) {"holoqa-desktop-engine.exe"} else {"holoqa-desktop-engine"}))
        };
        command.arg("--state-dir").arg(&state_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.spawn().map_err(|e| format!("Cannot start HoloQA engine: {e}"))?;
        let input = child.stdin.take().ok_or("Missing engine input")?;
        let output = child.stdout.take().ok_or("Missing engine output")?;
        let pending: Waiters = Arc::new(Mutex::new(HashMap::new()));
        let waiters = pending.clone();
        let handle = app.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                // Bound protocol frames, including base64 artifact previews.
                let mut line = Vec::new();
                let mut bounded = std::io::Read::take(&mut reader, 8 * 1024 * 1024);
                match bounded.read_until(b'\n', &mut line) {
                    Ok(0) | Err(_) => break,
                    Ok(_) if line.last() != Some(&b'\n') => break,
                    _ => {}
                }
                let Ok(message) = serde_json::from_slice::<Value>(&line) else { continue };
                if let Some(id) = message.get("id").and_then(Value::as_u64) {
                    if let Some(sender) = waiters.lock().unwrap().remove(&id) { let _ = sender.send(message); }
                } else if message.get("event").is_some() {
                    let _ = handle.emit("engine-event", message);
                }
            }
            waiters.lock().unwrap().clear();
            let _ = handle.emit("engine-event", json!({"event":"engine.disconnected","data":{}}));
        });
        Ok(Self {child:Mutex::new(child),input:Mutex::new(input),pending,sequence:AtomicU64::new(0)})
    }

    fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        let (tx, rx) = mpsc::channel();
        self.pending.lock().unwrap().insert(id, tx);
        let result = (|| {
            let mut input = self.input.lock().unwrap();
            writeln!(input, "{}", json!({"id":id,"method":method,"params":params})).map_err(|e|e.to_string())?;
            input.flush().map_err(|e|e.to_string())?;
            drop(input);
            let response = rx.recv_timeout(Duration::from_secs(90)).map_err(|_| "Engine did not respond. Check its state before retrying.".to_string())?;
            if let Some(error) = response.get("error") {
                Err(error.get("message").and_then(Value::as_str).unwrap_or("Engine request failed").to_string())
            } else { Ok(response["result"].clone()) }
        })();
        self.pending.lock().unwrap().remove(&id);
        result
    }

    fn shutdown(&self) {
        // Closing stdin triggers graceful cleanup. Windows kill-on-close Jobs
        // also own Codex descendants if the engine must be terminated.
        if let Ok(mut input) = self.input.lock() {
            let _ = writeln!(input, "{}", json!({"id":0,"method":"shutdown","params":{}}));
            let _ = input.flush();
        }
        if let Ok(mut child) = self.child.lock() {
            for _ in 0..20 {
                if child.try_wait().ok().flatten().is_some() { return; }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

// Startup errors remain visible in the UI instead of producing a blank window.
struct Backend(Result<Arc<Engine>, String>);
fn backend(state: State<'_, Backend>) -> Result<Arc<Engine>, String> { state.0.clone() }

#[tauri::command]
async fn engine_request(state: State<'_, Backend>, method: String, params: Value) -> Result<Value, String> {
    const METHODS: &[&str] = &["hello","project.recent_open","project.refresh","plan.load","plan.review","plan.create","provider.check","run.start","run.stop","run.snapshot","run.history","run.export","artifact.read","approval.respond"];
    if !METHODS.contains(&method.as_str()) { return Err("Unsupported desktop method".into()); }
    let engine = backend(state)?;
    tauri::async_runtime::spawn_blocking(move || engine.request(&method, params)).await.map_err(|e|e.to_string())?
}

#[tauri::command]
async fn open_project(app: tauri::AppHandle, state: State<'_, Backend>) -> Result<Value, String> {
    let engine = backend(state)?;
    tauri::async_runtime::spawn_blocking(move || {
        let picked = app.dialog().file().set_title("Open HoloQA project folder").blocking_pick_folder();
        let Some(picked) = picked else { return Ok(Value::Null) };
        let path = picked.into_path().map_err(|e|e.to_string())?;
        engine.request("project.open", json!({"path":path}))
    }).await.map_err(|e|e.to_string())?
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| { app.manage(Backend(Engine::spawn(app.handle()).map(Arc::new))); Ok(()) })
        .invoke_handler(tauri::generate_handler![engine_request, open_project])
        .build(tauri::generate_context!()).expect("Cannot create desktop application");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(backend) = handle.try_state::<Backend>() {
                if let Ok(engine) = &backend.0 { engine.shutdown(); }
            }
        }
    });
}
