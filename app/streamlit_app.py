"""App giám sát ESP32 (Wokwi) qua ThingsBoard Cloud — Bài thực hành số 2 IoT.

Chạy trên Streamlit Community Cloud. Secrets (Advanced settings -> Secrets):
    THINGSBOARD_URL = "https://thingsboard.cloud"
    TB_API_KEY = "<API key ThingsBoard>"
    DEVICE_ID = "<ID thiết bị>"
"""
import os
import time
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="Giám sát ESP32 - IoT Bài 2", page_icon="📡", layout="wide")

KEYS = ["distance_cm", "rssi", "sequence", "uptime_s"]
RANGE_CM = (2, 400)          # miền đo của HC-SR04
PERIOD_S = 5                 # chu kỳ gửi của ESP32
TZ = "Asia/Ho_Chi_Minh"


def conf(name, default=""):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


TB_URL = conf("THINGSBOARD_URL", "https://thingsboard.cloud").rstrip("/")
API_KEY = conf("TB_API_KEY")
DEVICE_ID = conf("DEVICE_ID")


@st.cache_data(ttl=4, show_spinner=False)
def fetch(minutes):
    """Lấy telemetry trong `minutes` phút gần nhất, ghép các key theo timestamp."""
    end = int(time.time() * 1000)
    params = {"keys": ",".join(KEYS), "startTs": end - minutes * 60_000, "endTs": end,
              "limit": 50_000, "agg": "NONE", "orderBy": "ASC"}
    t0 = time.perf_counter()
    r = requests.get(f"{TB_URL}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/values/timeseries",
                     headers={"X-Authorization": f"ApiKey {API_KEY}"}, params=params, timeout=30)
    latency_ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    rows = defaultdict(dict)
    for key, items in r.json().items():
        for it in items:
            rows[it["ts"]][key] = it["value"]
    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return pd.DataFrame(columns=KEYS), latency_ms
    df = df.apply(pd.to_numeric, errors="coerce").sort_index().reindex(columns=KEYS)
    df.index = pd.to_datetime(df.index, unit="ms", utc=True).tz_convert(TZ)
    df.index.name = "time"
    return df, latency_ms


def analyse(df):
    """Kiểm tra hợp lệ và tiền xử lý giống notebook."""
    out = df["distance_cm"].notna() & ~df["distance_cm"].between(*RANGE_CM)
    clean = df[~out].copy()
    clean["distance_rolling_mean"] = clean["distance_cm"].rolling(3, min_periods=1).mean()
    clean["distance_delta"] = clean["distance_cm"].diff().fillna(0)
    reboot = df["uptime_s"].diff() < 0                      # uptime giảm => khởi động lại
    gap = (df["sequence"].diff() - 1).where(~reboot)        # sequence nhảy số => mất bản tin
    dt = df.index.to_series().diff().dt.total_seconds()
    stats = {"samples": len(df), "valid": len(clean), "out_of_range": int(out.sum()),
             "lost": int(gap[gap > 0].sum()), "reboot": int(reboot.sum()),
             "period_s": float(dt.median()) if len(dt) > 1 else float("nan")}
    events = [{"Thời gian": t, "Sự kiện": "Khởi động lại",
               "Chi tiết": f"uptime_s → {int(df.loc[t, 'uptime_s'])} s, sequence → {int(df.loc[t, 'sequence'])}"}
              for t in df.index[reboot]]
    events += [{"Thời gian": t, "Sự kiện": "Mất bản tin", "Chi tiết": f"thiếu {int(n)} bản tin"}
               for t, n in gap[gap > 0].items()]
    events += [{"Thời gian": t, "Sự kiện": "Ngoài miền đo", "Chi tiết": f"distance_cm = {v}"}
               for t, v in df.loc[out, "distance_cm"].items()]
    return clean, out, stats, pd.DataFrame(events)


# ---------------- Sidebar ----------------
st.sidebar.title("⚙️ Tùy chọn")
minutes = st.sidebar.selectbox("Khoảng thời gian", [15, 30, 60, 180, 720], index=1,
                               format_func=lambda m: f"{m} phút" if m < 60 else f"{m // 60} giờ")
near_cm = st.sidebar.slider("Cảnh báo vật cản gần (cm)", 5, 200, 30)
weak_dbm = st.sidebar.slider("Cảnh báo Wi-Fi yếu (dBm)", -110, -50, -90)
refresh = st.sidebar.slider("Tự làm mới (giây)", 5, 60, 5)
st.sidebar.caption(f"Thiết bị: `{DEVICE_ID[:8]}…`" if DEVICE_ID else "")

st.title("📡 Giám sát ESP32 — HC-SR04 qua ThingsBoard")
st.caption("Wokwi ESP32 → MQTT → ThingsBoard Cloud → REST API → app này · Bài thực hành số 2 IoT ·  B23DCCN451")
if not API_KEY or not DEVICE_ID:
    st.error("Chưa cấu hình Secrets: THINGSBOARD_URL, TB_API_KEY, DEVICE_ID.")
    st.stop()


@st.fragment(run_every=refresh)
def live():
    try:
        df, latency_ms = fetch(minutes)
    except requests.HTTPError as e:
        st.error(f"ThingsBoard trả lỗi {e.response.status_code}: kiểm tra TB_API_KEY và DEVICE_ID.")
        return
    except requests.RequestException as e:
        st.error(f"Không kết nối được ThingsBoard: {e}")
        return
    if df.empty:
        st.warning(f"Không có dữ liệu trong {minutes} phút gần nhất — bật thiết bị trên Wokwi.")
        return

    clean, out, s, events = analyse(df)
    last = df.iloc[-1]
    age = (pd.Timestamp.now(tz=TZ) - df.index[-1]).total_seconds()
    online = age < 3 * PERIOD_S

    # ---- Trạng thái và giá trị mới nhất ----
    st.markdown(f"### {'🟢 Thiết bị đang gửi dữ liệu' if online else '🔴 Thiết bị ngừng gửi'}"
                f" · bản tin cuối {df.index[-1]:%H:%M:%S} ({age:.0f} s trước)")
    c = st.columns(5)
    c[0].metric("Khoảng cách", f"{last['distance_cm']:.1f} cm" if pd.notna(last["distance_cm"]) else "—")
    c[1].metric("Cường độ Wi-Fi", f"{last['rssi']:.0f} dBm" if pd.notna(last["rssi"]) else "—")
    c[2].metric("Số bản tin (sequence)", f"{last['sequence']:.0f}" if pd.notna(last["sequence"]) else "—")
    c[3].metric("Thời gian hoạt động", f"{last['uptime_s']:.0f} s" if pd.notna(last["uptime_s"]) else "—")
    c[4].metric("Truy vấn REST", f"{latency_ms:.0f} ms")

    # ---- Cảnh báo ----
    d = last["distance_cm"]
    if not online:
        st.error(f"⚠️ Không nhận được bản tin mới trong {age:.0f} s (chu kỳ bình thường {PERIOD_S} s).")
    if pd.notna(d) and not RANGE_CM[0] <= d <= RANGE_CM[1]:
        st.error(f"⚠️ Khoảng cách {d:.1f} cm nằm ngoài miền đo {RANGE_CM[0]}–{RANGE_CM[1]} cm của HC-SR04.")
    elif pd.notna(d) and d < near_cm:
        st.warning(f"⚠️ Phát hiện vật cản gần: {d:.1f} cm (< {near_cm} cm).")
    if pd.notna(last["rssi"]) and last["rssi"] < weak_dbm:
        st.warning(f"📶 Wi-Fi yếu: {last['rssi']:.0f} dBm (< {weak_dbm} dBm).")

    # ---- Biểu đồ ----
    left, right = st.columns([3, 2])
    fig = go.Figure()
    fig.add_scatter(x=df.index, y=df["distance_cm"], name="Dữ liệu thô", line=dict(color="#9aa5b1", width=1))
    fig.add_scatter(x=clean.index, y=clean["distance_rolling_mean"], name="Trung bình trượt 3 mẫu",
                    line=dict(color="#2a6fdb", width=2))
    if out.any():
        fig.add_scatter(x=df.index[out], y=df.loc[out, "distance_cm"], mode="markers", name="Ngoài miền đo",
                        marker=dict(color="#c23b3b", size=6))
    fig.add_hline(y=near_cm, line_dash="dot", line_color="#e8833a", annotation_text=f"ngưỡng {near_cm} cm")
    fig.update_layout(title="Khoảng cách HC-SR04", yaxis_title="cm", height=360, margin=dict(t=50, b=20),
                      legend=dict(orientation="h", y=-0.15))
    left.plotly_chart(fig, width="stretch")
    fig2 = go.Figure(go.Scatter(x=df.index, y=df["rssi"], line=dict(color="#3a9d5d", width=1.2)))
    fig2.add_hline(y=weak_dbm, line_dash="dot", line_color="#e8833a")
    fig2.update_layout(title="Cường độ tín hiệu Wi-Fi", yaxis_title="dBm", height=360, margin=dict(t=50, b=20))
    right.plotly_chart(fig2, width="stretch")

    # ---- Chất lượng dữ liệu ----
    st.markdown("#### Chất lượng dữ liệu")
    k = st.columns(6)
    k[0].metric("Mẫu nhận được", s["samples"])
    k[1].metric("Mẫu hợp lệ", s["valid"], f"{100 * s['valid'] / s['samples']:.1f} %", delta_color="off")
    k[2].metric("Ngoài miền đo", s["out_of_range"])
    k[3].metric("Mất bản tin", s["lost"])
    k[4].metric("Khởi động lại", s["reboot"])
    k[5].metric("Chu kỳ gửi (trung vị)", f"{s['period_s']:.2f} s")

    e1, e2 = st.columns([3, 2])
    with e1:
        st.markdown("**Sự kiện bất thường** (mới nhất trước)")
        if events.empty:
            st.success("Không có sự kiện bất thường trong khoảng thời gian này.")
        else:
            ev = events.sort_values("Thời gian", ascending=False).head(50)
            ev["Thời gian"] = ev["Thời gian"].dt.strftime("%H:%M:%S")
            st.dataframe(ev, hide_index=True, width="stretch", height=240)
    with e2:
        st.markdown("**Dữ liệu đã tiền xử lý**")
        export = clean.reset_index()
        export["time"] = export["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        st.download_button("⬇️ Tải CSV", export.to_csv(index=False).encode("utf-8-sig"),
                           file_name="esp32_processed.csv", mime="text/csv")
        st.dataframe(export.tail(8).iloc[::-1], hide_index=True, width="stretch", height=200)


live()
