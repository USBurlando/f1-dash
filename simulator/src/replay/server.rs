use std::{env, sync::Arc};
use anyhow::Error;
use axum::{
    Json, Router,
    extract::{
        State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    response::Response,
    routing::{MethodRouter, get},
};
use futures::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use tracing::{debug, error, info};

pub struct AppState {
    lines: Vec<String>,
}

pub async fn run(lines: Vec<String>) -> Result<(), Error> {
    let addr = env::var("ADDRESS")?;
    let app_state = Arc::new(AppState { lines });

    let negotiate_route: MethodRouter<Arc<AppState>> = axum::routing::post(handle_negotiate)
        .options(handle_negotiate_options);

    let app = Router::new()
        .route("/ws", get(handle_http))
        .route("/negotiate", negotiate_route)
        .with_state(app_state);

    info!(addr, "starting simulator replay server");
    axum::serve(TcpListener::bind(addr).await?, app).await?;
    Ok(())
}

async fn handle_negotiate_options() -> Response {
    use axum::http::header;
    use axum::response::IntoResponse;
    (
        [
            (header::ACCESS_CONTROL_ALLOW_ORIGIN, "*"),
            (header::ACCESS_CONTROL_ALLOW_METHODS, "POST, OPTIONS"),
            (header::ACCESS_CONTROL_ALLOW_HEADERS, "*"),
        ],
        "",
    )
        .into_response()
}

async fn handle_negotiate(_state: State<Arc<AppState>>) -> Json<Value> {
    info!("negotiate request received");
    Json(json!({
        "connectionToken": "simulator-token",
        "connectionId": "simulator-connection",
        "negotiateVersion": 1
    }))
}

async fn handle_http(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> Response {
    info!("received connection");
    ws.on_upgrade(|socket| handle_ws(socket, state))
}

const RS: &str = "\u{001E}";

async fn find_invocation_id(rx: &mut futures::stream::SplitStream<WebSocket>) -> Option<String> {
    while let Some(Ok(msg)) = rx.next().await {
        if let Message::Text(txt) = msg {
            for frame in txt.split(RS) {
                let frame = frame.trim();
                if frame.is_empty() { continue; }
                if let Ok(v) = serde_json::from_str::<Value>(frame) {
                    if v.get("target").and_then(|t| t.as_str()) == Some("Subscribe") {
                        return Some(
                            v.get("invocationId")
                                .and_then(|i| i.as_str())
                                .unwrap_or("0")
                                .to_string()
                        );
                    }
                }
            }
        }
    }
    None
}

async fn handle_ws(socket: WebSocket, state: Arc<AppState>) {
    let (mut tx, mut rx) = socket.split();

    // Step 1: wait for protocol handshake and ack
    if let Some(Ok(Message::Text(_))) = rx.next().await {
        let ack = format!("{{}}{}", RS);
        if tx.send(Message::text(ack)).await.is_err() {
            return;
        }
        debug!("handshake complete");
    }

    // Step 2: wait for Subscribe, get invocationId
    let invocation_id = match find_invocation_id(&mut rx).await {
        Some(id) => id,
        None => {
            error!("no subscribe received");
            return;
        }
    };

    // Step 3: send COMPLETION with initial state (line 0 of recording)
    let initial_state: Value = state.lines.first()
        .and_then(|l| serde_json::from_str(l).ok())
        .unwrap_or(json!({}));

    let completion = format!(
        "{}{}",
        serde_json::to_string(&json!({
            "type": 3,
            "invocationId": invocation_id,
            "result": initial_state
        })).unwrap(),
        RS
    );

    if tx.send(Message::text(completion)).await.is_err() {
        return;
    }
    info!("sent initial state, starting replay of {} lines...", state.lines.len());

    // Step 4: stream feed messages from recording
    tokio::select! {
        _ = async {
            for line in state.lines.iter().skip(1) {
                let line = line.trim();
                if line.is_empty() { continue; }
                if !line.contains("\"target\"") { continue; }
                match tx.send(Message::text(line)).await {
                    Ok(_) => {}
                    Err(e) => {
                        error!("error sending: {}", e);
                        break;
                    }
                }
                tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
            }
            info!("replay finished");
            let _ = tx.send(Message::Close(None)).await;
        } => {}
        _ = async {
            while let Some(Ok(msg)) = rx.next().await {
                if let Message::Close(_) = msg { break; }
            }
        } => {}
    }

    info!("connection closed");
}
