import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from shapely.geometry import Point
import osmnx as ox
import requests
from streamlit_folium import st_folium
import openai
import math
import os

# ──────────────────────────────
# ✅ 환경변수 불러오기 (Streamlit Cloud 호환에 저장된 키 사용)
# ──────────────────────────────
MAPBOX_TOKEN = st.secrets["MAPBOX_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ──────────────────────────────
# ✅ 데이터 로드
# ──────────────────────────────
gdf = gpd.read_file("cb_tour.shp").to_crs(epsg=4326)
gdf["lon"], gdf["lat"] = gdf.geometry.x, gdf.geometry.y
boundary = gpd.read_file("cb_shp.shp").to_crs(epsg=4326)
data = pd.read_csv("cj_data_final.csv", encoding="cp949").drop_duplicates()





# csv 파일에 카페 있을때 출력 / 카페 포맷 함수
def format_cafes(cafes_df):
    cafes_df = cafes_df.drop_duplicates(subset=['c_name', 'c_value', 'c_review'])
    result = []

    if len(cafes_df) == 0:
        return ("현재 이 관광지 주변에 등록된 카페 정보는 없어요.  \n"
                "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니,  \n"
                "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊")

    elif len(cafes_df) == 1:
        row = cafes_df.iloc[0]
        if all(x not in row["c_review"] for x in ["없음", "없읍"]):
            return f"""☕ **주변 추천 카페**\n\n- **{row['c_name']}** (⭐ {row['c_value']})  \n“{row['c_review']}”"""
        else:
            return f"""☕ **주변 추천 카페**\n\n- **{row['c_name']}** (⭐ {row['c_value']})"""

    else:
        grouped = cafes_df.groupby(['c_name', 'c_value'])
        result.append("**주변의 평점 높은 카페들은 여기 있어요!** 🌼\n")
        for (name, value), group in grouped:
            reviews = group['c_review'].dropna().unique()
            reviews = [r for r in reviews if all(x not in r for x in ["없음", "없읍"])]
            top_reviews = reviews[:3]

            if top_reviews:
                review_text = "\n".join([f"“{r}”" for r in top_reviews])
                result.append(f"- **{name}** (⭐ {value})  \n{review_text}")
            else:
                result.append(f"- **{name}** (⭐ {value})")

        return "\n\n".join(result)





# ──────────────────────────────
# ✅ Session 초기화
# ──────────────────────────────
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 청주 문화관광 전문 가이드입니다."}],
    "auto_gpt_input": ""
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ──────────────────────────────
# ✅ CSS & 스타일
# ──────────────────────────────
st.set_page_config(page_title="청주시 경유지 & GPT 가이드", layout="wide")
st.markdown("""
<style>
  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background: #f9fafb;
    color: #333;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    margin-bottom: 20px;
  }
  .stButton>button {
    border-radius: 8px;
    font-weight: 600;
    padding: 12px 24px;
    width: 100%;
  }
  .btn-create { background: linear-gradient(90deg,#00C9A7,#008EAB); color: #fff; }
  .btn-clear { background: #E63946; color: #fff; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 상단 타이틀
# ──────────────────────────────
st.markdown("<h1 style='text-align:center;'>📍 청풍로드</h1>", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 컬럼: 좌 → 우 UX 흐름
# ──────────────────────────────
col1, col2, col3, col4 = st.columns([1.5, 1, 1, 3], gap="large")

# ------------------------------
# ✅ [좌] 경로 설정
# ------------------------------
with col1:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🚗 경로 설정")

    # ✅ 키 지정해서 초기화 가능하게 만들기
    mode = st.radio("이동 모드", ["driving", "walking"], horizontal=True, key="mode_key")
    start = st.selectbox("출발지", gdf["name"].dropna().unique(), key="start_key")
    wps = st.multiselect("경유지", [n for n in gdf["name"].dropna().unique() if n != st.session_state["start_key"]], key="wps_key")

    create_clicked = st.button("✅ 경로 생성")
    clear_clicked = st.button("🚫 초기화")  # 초기화 버튼 유지

    st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# ✅ 초기화 처리 (버튼 누를 때)
# ------------------------------
if clear_clicked:
    # 상태값 초기화
    for k in ["segments", "order", "duration", "distance", "auto_gpt_input"]:
        st.session_state.pop(k, None)

    # 위젯 값도 초기화
    for widget_key in ["mode_key", "start_key", "wps_key"]:
        st.session_state.pop(widget_key, None)

    # rerun으로 적용
    st.rerun()

# ------------------------------
# ✅ [중간] 방문 순서
# ------------------------------
with col2:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🔢 방문 순서")
    if st.session_state["order"]:
        for i, name in enumerate(st.session_state["order"], 1):
            st.markdown(f"{i}. {name}")
    else:
        st.markdown("<span style='color:#aaa'>경로 생성 후 표시됩니다.</span>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------
# ✅ [중간] KPI 카드
# ------------------------------
with col3:
    st.markdown("<div class='card'>예상 소요 시간</div>", unsafe_allow_html=True)
    st.subheader(f"{st.session_state['duration']:.1f} 분")
    st.markdown("<div class='card'>예상 이동 거리</div>", unsafe_allow_html=True)
    st.subheader(f"{st.session_state['distance']:.2f} km")

# ------------------------------
# ✅ [우] 지도 + GPT
# ------------------------------
with col4:
    ctr = boundary.geometry.centroid
    clat, clon = float(ctr.y.mean()), float(ctr.x.mean())
    if math.isnan(clat): clat, clon = 36.64, 127.48

    @st.cache_data
    def load_graph(lat, lon):
        return ox.graph_from_point((lat, lon), dist=3000, network_type="all")
    G = load_graph(clat, clon)

    stops = [start] + wps
    snapped = []
    for nm in stops:
        row = gdf[gdf["name"] == nm].iloc[0]
        lon, lat = row.lon, row.lat
        node_id = ox.nearest_nodes(G, lon, lat)
        node_data = G.nodes[node_id]
        snapped.append((node_data['x'], node_data['y']))

    if clear_clicked:
        for k in ["segments", "order", "duration", "distance"]:
            st.session_state.pop(k, None)

    if create_clicked and len(snapped) >= 2:
        coords = ";".join([f"{x},{y}" for x, y in snapped])
        url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/{mode}/{coords}" if len(snapped) > 2 \
            else f"https://api.mapbox.com/directions/v5/mapbox/{mode}/{coords}"
        key = "trips" if len(snapped) > 2 else "routes"
        params = {
            "geometries": "geojson", "overview": "full",
            "source": "first", "destination": "last", "roundtrip": "false",
            "access_token": MAPBOX_TOKEN
        } if len(snapped) > 2 else {
            "geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN
        }
        r = requests.get(url, params=params)
        data_resp = r.json() if r.status_code == 200 else {}
        if key in data_resp and data_resp[key]:
            route = data_resp[key][0]
            st.session_state["segments"] = [route["geometry"]["coordinates"]]
            st.session_state["duration"] = route["duration"] / 60
            st.session_state["distance"] = route["distance"] / 1000
            st.session_state["order"] = stops
        else:
            st.warning("❌ 경로 생성 실패!")

    m = folium.Map(location=[clat, clon], zoom_start=12)
    folium.GeoJson(boundary).add_to(m)
    mc = MarkerCluster().add_to(m)
    for _, row in gdf.iterrows():
        folium.Marker([row.lat, row.lon], popup=row.name).add_to(mc)
    for idx, ((x, y), name) in enumerate(zip(snapped, st.session_state.get("order", stops)), 1):
        folium.Marker([y, x], tooltip=f"{idx}. {name}",
                      icon=folium.Icon(color="#008EAB", icon="flag")).add_to(m)
    if st.session_state["segments"]:
        for seg in st.session_state["segments"]:
            folium.PolyLine([(pt[1], pt[0]) for pt in seg], color="red").add_to(m)
    st_folium(m, width="100%", height=400)

      # OpenAI 클라이언트 초기화
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])









# ------------------------------
# ✅ GPT 가이드
# ------------------------------


####### 현재 GPT 가이드는 토큰 제한으로 인해 출발지 포함 3개까지만 관광지를 호출할 수 있습니다 ######

# GPT 가이드 UI
st.markdown("---")
st.subheader("🏛️ AI 관광 가이드")

# 버튼 누르면 자동 입력값 저장
if st.button("🔁 방문 순서 자동 입력"):
    st.session_state["auto_gpt_input"] = ", ".join(st.session_state.get("order", []))

# 메시지 상태 초기화 (한 번만 실행됨)
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# 입력 폼 구성
with st.form("chat_form"):
    user_input = st.text_input("관광지명 쉼표로 구분", value=st.session_state.get("auto_gpt_input", ""))
    submitted = st.form_submit_button("click!")

# 폼 제출되었을 때 GPT 호출
if submitted and user_input:

    if st.session_state["order"]:
        st.markdown("## ✨ 관광지별 소개 + 카페 추천")

        # 최대 3개까지만 처리
        for place in st.session_state["order"][:3]:
            matched = data[data['t_name'].str.contains(place, na=False)]

            # GPT 간략 소개 with 예외 처리
            try:
                gpt_intro = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "당신은 청주 지역의 문화 관광지를 간단하게 소개하는 관광 가이드입니다. "},
                        {"role": "system", "content": "존댓말을 사용하세요."},
                        {"role": "user", "content": f"{place}를 두 문단 이내로 간단히 설명해주세요."}
                    ]
                ).choices[0].message.content
            except Exception as e:
                gpt_intro = f"❌ GPT 호출 실패: {place} 소개를 불러올 수 없어요."

            score_text = ""
            review_block = ""
            cafe_info = ""

            if not matched.empty:
                # 평점
                t_value = matched['t_value'].dropna().unique()
                score_text = f"📊 관광지 평점: ⭐ {t_value[0]}" if len(t_value) > 0 else ""

                # 리뷰
                reviews = matched['t_review'].dropna().unique()
                reviews = [r for r in reviews if all(x not in r for x in ["없음", "없읍"])]
                if reviews:
                    review_text = "\n".join([f"“{r}”" for r in reviews[:3]])
                    review_block = f"💬 방문자 리뷰\n{review_text}"

                # 카페
                cafes = matched[['c_name', 'c_value', 'c_review']].drop_duplicates()
                cafe_info = format_cafes(cafes)
            else:
                cafe_info = (
                    "현재 이 관광지 주변에 등록된 카페 정보는 없어요.  \n"
                    "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니,  \n"
                    "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊"
                )

            # ✅ 반복문 안에서 출력
            response_lines = []
            response_lines.append("---")
            response_lines.append(f"🏛️ **{place}**")
            if score_text:
                response_lines.append(score_text)
            response_lines.append("✨ **소개**")
            response_lines.append(gpt_intro.strip())
            if cafe_info:
                response_lines.append("🧋 **주변 카페 추천**")
                response_lines.append(cafe_info.strip())
            if review_block:
                response_lines.append("💬 **방문자 리뷰**")
                for r in review_text.split("\n"):
                    response_lines.append(f"- {r.strip('“”')}")

            st.markdown("\n\n".join(response_lines))

    # st.session_state["messages"].append({"role": "user", "content": user_input})
    
    




    # response = client.chat.completions.create(
    #     model="gpt-3.5-turbo",
    #     messages=[
    #         {"role": "system", "content": "너는 청주 관광 가이드야."},
    #         {"role": "user", "content": user_input}
    #     ]
    # )

    # gpt_reply = response.choices[0].message.content
    # st.markdown(f"**🗺️ GPT 답변:** {gpt_reply}")

    # for msg in st.session_state["messages"][1:]:
    #     align = "right" if msg["role"] == "user" else "left"
    #     bg = "#dcf8c6" if msg["role"] == "user" else "#fff"
    #     st.markdown(f"<div style='text-align:{align};background:{bg};padding:8px;border-radius:10px;margin-bottom:6px'>{msg['content']}</div>", unsafe_allow_html=True)
