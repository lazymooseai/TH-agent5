import streamlit as st
from datetime import datetime, timedelta
import pytz
import requests
from bs4 import BeautifulSoup
import random

# --- KONFIGURAATIO ---
st.set_page_config(page_title="TH Agentti", page_icon="🚕", layout="centered", initial_sidebar_state="collapsed")
HELSINKI_TZ = pytz.timezone('Europe/Helsinki')

# --- TILANHALLINTA ---
if 'event_states' not in st.session_state:
    st.session_state.event_states = {"Messukeskus": "NORMAALI", "Jäähalli": "NORMAALI", "Ooppera": "NORMAALI"}
if 'selected_station' not in st.session_state:
    st.session_state.selected_station = "HELSINKI"
if 'train_limit' not in st.session_state:
    st.session_state.train_limit = 3

def update_status(event_name, new_status):
    st.session_state.event_states[event_name] = new_status

# --- FINTRAFFIC METADATA (ASEMIEN NIMET) ---
@st.cache_data(ttl=86400)
def get_station_names():
    try:
        res = requests.get("https://rata.digitraffic.fi/api/v1/metadata/stations", timeout=5)
        if res.status_code == 200:
            return {s['stationShortCode']: s['stationName'].replace(" asema", "") for s in res.json()}
    except: pass
    return {}

# --- FINTRAFFIC API (JUNAT - LAAJENNETTU TUTKA) ---
@st.cache_data(ttl=60)
def fetch_live_trains(station_name):
    station_codes = {"HELSINKI": "HKI", "PASILA": "PSL", "TIKKURILA": "TKL"}
    code = station_codes.get(station_name, "HKI")
    station_names = get_station_names()
    
    # Haetaan 150 seuraavaa junaa, jotta yölläkin riittää dataa aamuun asti
    url = f"https://rata.digitraffic.fi/api/v1/live-trains/station/{code}?arriving_trains=150&departing_trains=0&include_nonstopping=false"
    
    try:
        res = requests.get(url, timeout=5)
        if res.status_code != 200: return []
        trains = res.json()
        results = []
        for t in trains:
            if t.get('trainCategory') == 'Long-distance':
                timetable = t.get('timeTableRows', [])
                if not timetable: continue
                
                final_station = timetable[-1].get('stationShortCode')
                if final_station != 'HKI': continue
                
                origin_code = timetable[0].get('stationShortCode')
                origin_name = station_names.get(origin_code, origin_code)
                
                for row in timetable:
                    if row.get('stationShortCode') == code and row.get('type') == 'ARRIVAL':
                        scheduled_utc = datetime.fromisoformat(row['scheduledTime'].replace('Z', '+00:00'))
                        scheduled_hel = scheduled_utc.astimezone(HELSINKI_TZ)
                        live_estimate = row.get('liveEstimateTime')
                        
                        now = datetime.now(HELSINKI_TZ)
                        if scheduled_hel < now - timedelta(minutes=10): continue

                        if live_estimate:
                            est_utc = datetime.fromisoformat(live_estimate.replace('Z', '+00:00'))
                            est_hel = est_utc.astimezone(HELSINKI_TZ)
                            diff_minutes = (est_hel - scheduled_hel).total_seconds() / 60
                            status = f"<span style='color: #F87171;'>Myöhässä (Arvio {est_hel.strftime('%H:%M')})</span>" if diff_minutes > 2 else "Aikataulussa"
                        else:
                            status = "Aikataulussa"
                            
                        results.append({
                            "id": f"{t.get('trainType', '')} {t.get('trainNumber', '')}",
                            "route": f"Saapuu ({origin_name} -> HKI)", 
                            "time": scheduled_hel.strftime('%H:%M'),
                            "status": status, 
                            "sort_time": scheduled_hel
                        })
                        break
                        
        results.sort(key=lambda x: x['sort_time'])
        return results[:7] # Palautetaan maksimissaan 7
    except: return []

# --- AVERIO / LAIVADATA ---
@st.cache_data(ttl=300)
def fetch_live_ships():
    ship_database = {
        "MS Finlandia": {"max": 2080, "terminal": "Länsiterminaali T2"},
        "Finlandia": {"max": 2080, "terminal": "Länsiterminaali T2"}, 
        "MyStar": {"max": 2800, "terminal": "Länsiterminaali T2"},
        "Megastar": {"max": 2800, "terminal": "Länsiterminaali T2"},
        "Viking XPRS": {"max": 2500, "terminal": "Katajanokka"},
        "Silja Serenade": {"max": 2852, "terminal": "Olympiaterminaali"},
        "Gabriella": {"max": 2400, "terminal": "Katajanokka"},
        "Viking Cinderella": {"max": 2560, "terminal": "Katajanokka"}
    }
    ships = []
    now = datetime.now(HELSINKI_TZ)
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get("https://averio.fi/laivat/", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            for tr in soup.find_all('tr'):
                tds = tr.find_all(['td', 'th'])
                if len(tds) >= 4:
                    time_str = tds[0].text.strip()
                    ship_name = tds[2].text.strip()
                    pax_str = tds[3].text.strip()
                    
                    if "Saapumisaika" in time_str or not ship_name: continue
                    clean_name = ship_name
                    if clean_name in ship_database:
                        only_time = time_str.split(' ')[0] if ' ' in time_str else time_str
                        try:
                            h, m = map(int, only_time.split(':'))
                            ship_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                            if (now - ship_dt).total_seconds() > 10 * 3600: continue
                            if ship_dt < now - timedelta(minutes=45) and (now - ship_dt).total_seconds() > 0: continue
                        except: pass
                        
                        pax_digits = ''.join(filter(str.isdigit, pax_str))
                        if pax_digits:
                            pax_count = int(pax_digits)
                            if pax_count > 0:
                                max_cap = ship_database[clean_name]["max"]
                                rate = min(100, int((pax_count / max_cap) * 100))
                                bar_color = "#F87171" if rate > 80 else "#FBBF24" if rate > 50 else "#4ADE80"
                                ships.append({
                                    "name": clean_name, "terminal": ship_database[clean_name]["terminal"],
                                    "max": max_cap, "pax": pax_count, "rate": rate,
                                    "bar_color": bar_color, "time": only_time,
                                    "source": "LÄHDE: AVERIO.FI (LIVE)"
                                })
    except: pass
        
    if len(ships) > 0: return ships[:3]

    for name, data in list(ship_database.items())[2:4]:
        base_fill = 0.4 if now.hour < 14 else 0.7
        occupancy_rate = min(0.98, max(0.2, base_fill + random.uniform(-0.1, 0.2)))
        estimated_pax = int(data["max"] * occupancy_rate)
        occupancy_percentage = int(occupancy_rate * 100)
        bar_color = "#F87171" if occupancy_percentage > 80 else "#FBBF24" if occupancy_percentage > 50 else "#4ADE80"
        ships.append({
            "name": name, "terminal": data["terminal"], "max": data["max"],
            "pax": estimated_pax, "rate": occupancy_percentage, "bar_color": bar_color,
            "time": f"{(now.hour + 1) % 24:02d}:30", "source": "LÄHDE: TH ALGORITMI (FALLBACK)"
        })
    return ships

# --- CSS INJEKTIO ---
st.markdown("""
<style>
    .stApp { background-color: #0F111A; color: #E2E8F0; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
    #MainMenu, header, footer {visibility: hidden;}
    .block-container { padding-top: 1rem; padding-bottom: 5rem; }
    .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .time-display { font-size: 2.5rem; font-weight: 800; color: #FFFFFF; letter-spacing: -1px; }
    .time-display span { color: #4ADE80; }
    .weather-widget { background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 8px; text-align: right; }
    .weather-temp { font-size: 1.2rem; font-weight: 700; color: #FFFFFF; }
    .weather-desc { font-size: 0.7rem; color: #94A3B8; text-transform: uppercase; letter-spacing: 1px;}
    .th-card { background: #191B24; border: 1px solid #2D313E; border-radius: 12px; padding: 16px; margin-bottom: 12px; position: relative; }
    .th-card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: #4ADE80; border-radius: 12px 0 0 12px; }
    .th-card.red-border::before { background: #F87171; }
    .th-card.yellow-border::before { background: #FBBF24; }
    .card-title { font-size: 1.1rem; font-weight: 700; color: #FFFFFF; margin-bottom: 2px; z-index: 2; position: relative;}
    .card-subtitle { font-size: 0.85rem; color: #94A3B8; margin-bottom: 8px; z-index: 2; position: relative;}
    .card-time { position: absolute; right: 16px; top: 16px; font-size: 1.8rem; font-weight: 800; color: #4ADE80; letter-spacing: -1px; z-index: 2;}
    .card-time.red-text { color: #F87171; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 800; text-transform: uppercase; margin-top: 8px; z-index: 2; position: relative;}
    .badge-info { background: rgba(148, 163, 184, 0.15); color: #94A3B8; }
    .live-dot { height: 8px; width: 8px; background-color: #4ADE80; border-radius: 50%; display: inline-block; margin-right: 4px; animation: pulse 2s infinite;}
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    .card-link { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; text-decoration: none; }
    .link-icon { position: absolute; right: 16px; bottom: 16px; color: #64748B; font-size: 1.2rem; z-index: 2;}
    .section-title { font-size: 0.85rem; font-weight: 800; color: #94A3B8; text-transform: uppercase; margin: 24px 0 12px 0; letter-spacing: 1px; display: flex; align-items: center;}
    .progress-bg { background-color: #2D313E; border-radius: 4px; height: 6px; width: 100%; margin-top: 6px; overflow: hidden; }
    .progress-bar { height: 100%; border-radius: 4px; transition: width 0.5s ease; }
    .pax-stats { display: flex; justify-content: space-between; font-size: 0.75rem; color: #94A3B8; margin-top: 4px; font-weight: 600;}
</style>
""", unsafe_allow_html=True)

# --- 1. YLÄPALKKI & SÄÄ ---
now = datetime.now(HELSINKI_TZ)
time_str = now.strftime("%H") + "<span>:</span>" + now.strftime("%M")
st.markdown(f"""
<div class="top-bar">
    <div class="time-display">{time_str}</div>
    <div class="weather-widget">
        <div class="weather-temp">🌦️ +5°</div>
        <div class="weather-desc">SADE ALKAMASSA (Kysyntä 1.4x)</div>
    </div>
</div>
""", unsafe_allow_html=True)

# --- 2. SATAMAT (LAIVAT & TÄYTTÖASTE) ---
st.markdown('<div class="section-title">⛴️ SATAMAT (LAIVAT)</div>', unsafe_allow_html=True)
live_ships = fetch_live_ships()
for ship in live_ships:
    st.markdown(f"""
    <div class="th-card">
        <a href="https://averio.fi/laivat/" target="_blank" class="card-link"></a>
        <div class="card-title">{ship['name']} <span class="live-dot" style="margin-left: 8px;"></span><span style="font-size: 0.6rem; color: #4ADE80;">LIVE</span></div>
        <div class="card-subtitle">{ship['terminal']}</div>
        <div class="card-time">{ship['time']}</div>
        <div style="margin-top: 12px; z-index: 2; position: relative;">
            <div class="progress-bg"><div class="progress-bar" style="width: {ship['rate']}%; background-color: {ship['bar_color']};"></div></div>
            <div class="pax-stats"><span>Arvioitu purku: <span style="color: #E2E8F0;">{ship['pax']} hlö</span></span><span>Täyttöaste: <span style="color: {ship['bar_color']};">{ship['rate']}%</span> (Max: {ship['max']})</span></div>
        </div>
        <div class="badge badge-info">{ship['source']}</div>
        <div class="link-icon">↗</div>
    </div>
    """, unsafe_allow_html=True)

# --- 3. LIVE-JUNAT (FINTRAFFIC API) ---
st.markdown('<div class="section-title">🚆 JUNAT (KAUKO)</div>', unsafe_allow_html=True)

# Kun asemaa vaihdetaan, palautetaan näkymä automaattisesti 3 junaan
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("HELSINKI", use_container_width=True, type="primary" if st.session_state.selected_station == "HELSINKI" else "secondary"): 
        st.session_state.selected_station = "HELSINKI"
        st.session_state.train_limit = 3
        st.rerun()
with col2:
    if st.button("PASILA", use_container_width=True, type="primary" if st.session_state.selected_station == "PASILA" else "secondary"): 
        st.session_state.selected_station = "PASILA"
        st.session_state.train_limit = 3
        st.rerun()
with col3:
    if st.button("TIKKURILA", use_container_width=True, type="primary" if st.session_state.selected_station == "TIKKURILA" else "secondary"): 
        st.session_state.selected_station = "TIKKURILA"
        st.session_state.train_limit = 3
        st.rerun()

live_trains = fetch_live_trains(st.session_state.selected_station)

if not live_trains:
    st.markdown("<p style='color:#94A3B8'>Ei saapuvaa kaukoliikennettä.</p>", unsafe_allow_html=True)
else:
    # Näytetään junat joko 3 tai 7 mukaan
    limit = st.session_state.train_limit
    for train in live_trains[:limit]:
        border_class = "red-border" if "Myöhässä" in train['status'] else ""
        time_class = "red-text" if "Myöhässä" in train['status'] else ""
        st.markdown(f"""
        <div class="th-card {border_class}">
            <div class="card-title">{train['id']}</div>
            <div class="card-subtitle">{train['route']} • {train['status']}</div>
            <div class="card-time {time_class}">{train['time']}</div>
        </div>
        """, unsafe_allow_html=True)
        
    # Kognitiivinen kytkin: Näytä lisää / Näytä vähemmän
    if len(live_trains) > 3:
        if st.session_state.train_limit == 3:
            if st.button("Näytä 7 seuraavaa junaa ⬇", use_container_width=True):
                st.session_state.train_limit = 7
                st.rerun()
        else:
            if st.button("Näytä vain 3 junaa ⬆", use_container_width=True):
                st.session_state.train_limit = 3
                st.rerun()

# --- 4. TAPAHTUMAT TÄNÄÄN ---
st.markdown('<div class="section-title">🎫 TAPAHTUMAT TÄNÄÄN</div>', unsafe_allow_html=True)
events = [
    {"id": "Messukeskus", "title": "Kevätmessut", "location": "Messukeskus", "time": "17:00", "duration": "Ovet sulkeutuvat", "badge_class": "badge-info", "badge_text": "SUURI TAPAHTUMA", "border_class": "", "time_class": "", "url": "https://messukeskus.com/kavijalle/tapahtumat/tapahtumakalenteri"},
    {"id": "Jäähalli", "title": "Jääkiekko: HIFK - Kärpät", "location": "Helsingin Jäähalli", "time": "18:30", "duration": "150 min", "badge_class": "badge-fire", "badge_text": "KORKEA KYSYNTÄ 🔥", "border_class": "red-border", "time_class": "red-text", "url": "https://liiga.fi/fi/ohjelma?kausi=2025-2026&sarja=runkosarja&joukkue=hifk&kotiVieras=koti"},
    {"id": "Ooppera", "title": "Oopperaesitys (Tosca)", "location": "Kansallisooppera", "time": "19:00", "duration": "180 min", "badge_class": "badge-premium", "badge_text": "PREMIUM (PUKU PÄÄLLÄ)", "border_class": "yellow-border", "time_class": "yellow-text", "url": "https://oopperabaletti.fi/kalenteri/"}
]

for ev in events:
    current_state = st.session_state.event_states[ev["id"]]
    state_display = '<span style="color: #F87171; font-weight: bold; margin-left: 10px;">[🚕 JONOA]</span>' if current_state == "JONO!" else '<span style="color: #64748B; font-weight: bold; margin-left: 10px;">[✓ PURETTU]</span>' if current_state == "OHI" else ''
    st.markdown(f"""
    <div class="th-card {ev['border_class']}">
        <a href="{ev['url']}" target="_blank" class="card-link" style="height: 60%;"></a>
        <div class="card-subtitle" style="text-transform: uppercase;">AJOITUS: PURKU</div>
        <div class="card-title">{ev['title']} {state_display}</div>
        <div class="card-subtitle">{ev['location']}<br>Loppuu: {ev['time']} ({ev['duration']})</div>
        <div class="card-time {ev['time_class']}">{ev['time']}</div>
        <div class="badge {ev['badge_class']}">{ev['badge_text']}</div>
        <div class="link-icon" style="top: 16px; bottom: auto;">↗</div>
    </div>
    """, unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("✓ OHI", key=f"btn_ohi_{ev['id']}", use_container_width=True, type="secondary" if current_state != "OHI" else "primary"): update_status(ev["id"], "OHI"); st.rerun()
    with c2:
        if st.button("➖ NORMAALI", key=f"btn_norm_{ev['id']}", use_container_width=True, type="secondary" if current_state != "NORMAALI" else "primary"): update_status(ev["id"], "NORMAALI"); st.rerun()
    with c3:
        if st.button("⚠️ JONO!", key=f"btn_jono_{ev['id']}", use_container_width=True, type="secondary" if current_state != "JONO!" else "primary"): update_status(ev["id"], "JONO!"); st.rerun()
    st.markdown("<div style='margin-bottom: 20px;'></div>", unsafe_allow_html=True)
st.divider()
