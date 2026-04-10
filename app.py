from datetime import datetime
import math
import os

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates", static_folder="static")

# Read API key only from environment to avoid committing secrets.
API_KEY = os.getenv("OPENWEATHER_API_KEY")

CITY_TO_ICAO = {
    "san francisco": "KSFO",
    "new york": "KJFK",
    "london": "EGLL",
    "tokyo": "RJTT",
    "sydney": "YSSY",
    "paris": "LFPG",
    "dubai": "OMDB",
    "mumbai": "VABB",
}


def _wind_direction(degrees):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round((degrees % 360) / 45) % 8
    return directions[idx]


def _icon_to_emoji(icon_id):
    if not icon_id:
        return "☁"

    code = icon_id[:2]
    mapping = {
        "01": "☀",
        "02": "⛅",
        "03": "☁",
        "04": "☁",
        "09": "🌧",
        "10": "🌦",
        "11": "⛈",
        "13": "❄",
        "50": "🌫",
    }
    return mapping.get(code, "☁")


def _build_hourly_series(base_temp, base_rain):
    hourly_temps = []
    hourly_rain = []
    for hour in range(24):
        wave = math.sin(((hour - 14) / 24) * 2 * math.pi)
        hourly_temps.append(round(base_temp + 3 * wave, 1))
        rain_wave = max(0, math.sin(((hour - 10) / 24) * 2 * math.pi))
        hourly_rain.append(round(base_rain * (0.4 + rain_wave), 1))
    return hourly_temps, hourly_rain


def _resolve_icao(city):
    cleaned = city.strip().lower()
    if cleaned in CITY_TO_ICAO:
        return CITY_TO_ICAO[cleaned]

    if "," in cleaned:
        first_part = cleaned.split(",", 1)[0].strip()
        if first_part in CITY_TO_ICAO:
            return CITY_TO_ICAO[first_part]

    # If user enters a station directly, allow it.
    if city.isalpha() and len(city) == 4:
        return city.upper()

    return None


def _fetch_metar_taf(icao):
    if not icao:
        return (
            "No ICAO station mapping available for this city.",
            "No ICAO station mapping available for this city.",
            "--:--",
            "N/A",
        )

    metar_text = "METAR not available right now."
    taf_text = "TAF not available right now."
    metar_time = "--:--"
    taf_valid = "N/A"

    try:
        metar_response = requests.get(
            f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json",
            timeout=10,
        )
        metar_data = metar_response.json()
        if metar_data:
            metar_record = metar_data[0]
            metar_text = metar_record.get("rawOb") or metar_text
            report_time = metar_record.get("reportTime", "")
            if report_time:
                metar_time = report_time[11:16]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        pass

    try:
        taf_response = requests.get(
            f"https://aviationweather.gov/api/data/taf?ids={icao}&format=json",
            timeout=10,
        )
        taf_data = taf_response.json()
        if taf_data:
            taf_record = taf_data[0]
            taf_text = taf_record.get("rawTAF") or taf_text
            valid_from = taf_record.get("validTimeFrom")
            valid_to = taf_record.get("validTimeTo")
            if valid_from and valid_to:
                taf_valid = (
                    f"{datetime.utcfromtimestamp(valid_from).strftime('%d %b %H:%M')}"
                    f" to {datetime.utcfromtimestamp(valid_to).strftime('%d %b %H:%M')} UTC"
                )
    except (requests.RequestException, ValueError, TypeError, KeyError):
        pass

    return metar_text, taf_text, metar_time, taf_valid


def _fetch_weather(city):
    if not API_KEY:
        return None, "Server is missing OPENWEATHER_API_KEY."

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={city}&appid={API_KEY}&units=metric"
    )

    response = requests.get(url, timeout=10)
    data = response.json()
    cod = str(data.get("cod", ""))

    if cod != "200":
        return None, data.get("message", "Could not fetch weather for that city.")

    temp = float(data["main"].get("temp", 0))
    rain = float(data.get("rain", {}).get("1h", 0))
    hourly_temps, hourly_rain = _build_hourly_series(temp, rain)

    wind_speed = round(float(data.get("wind", {}).get("speed", 0)) * 3.6)
    wind_gust = round(float(data.get("wind", {}).get("gust", 0)) * 3.6)
    wind_deg = float(data.get("wind", {}).get("deg", 0))
    icon_id = data["weather"][0].get("icon", "")
    icao = _resolve_icao(city)
    metar, taf, metar_time, taf_valid = _fetch_metar_taf(icao)
    timezone_offset = int(data.get("timezone", 0))
    observation_ts = int(data.get("dt", int(datetime.utcnow().timestamp())))
    city_name = data.get("name") or city.title()
    country = data.get("sys", {}).get("country", "")
    city_label = f"{city_name}, {country}" if country else city_name

    payload = {
        "city": city_label,
        "condition": data["weather"][0].get("description", "Unknown").title(),
        "temp": round(temp),
        "hi": round(float(data["main"].get("temp_max", temp))),
        "lo": round(float(data["main"].get("temp_min", temp))),
        "humidity": int(data["main"].get("humidity", 0)),
        "pressure": int(data["main"].get("pressure", 0)),
        "rain": round(rain, 1),
        "wind": wind_speed,
        "gust": wind_gust,
        "windDir": _wind_direction(wind_deg),
        "icon": _icon_to_emoji(icon_id),
        "icon_url": f"https://openweathermap.org/img/w/{icon_id}.png",
        "updated_time": datetime.now().strftime("%A, %B %d · %I:%M %p"),
        "timezoneOffset": timezone_offset,
        "obsTs": observation_ts,
        "hourlyTemps": hourly_temps,
        "hourlyRain": hourly_rain,
        "metar": metar,
        "taf": taf,
        "metarTime": metar_time,
        "tafValid": taf_valid,
        "alert": rain >= 4,
        "alertMsg": (
            "Heavy rain expected. Plan travel with caution." if rain >= 4 else ""
        ),
    }
    return payload, None


def _fetch_weather_by_coords(lat, lon):
    if not API_KEY:
        return None, "Server is missing OPENWEATHER_API_KEY."

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lon}&appid={API_KEY}&units=metric"
    )

    response = requests.get(url, timeout=10)
    data = response.json()
    cod = str(data.get("cod", ""))

    if cod != "200":
        return None, data.get("message", "Could not fetch weather for your location.")

    temp = float(data["main"].get("temp", 0))
    rain = float(data.get("rain", {}).get("1h", 0))
    hourly_temps, hourly_rain = _build_hourly_series(temp, rain)

    wind_speed = round(float(data.get("wind", {}).get("speed", 0)) * 3.6)
    wind_gust = round(float(data.get("wind", {}).get("gust", 0)) * 3.6)
    wind_deg = float(data.get("wind", {}).get("deg", 0))
    icon_id = data["weather"][0].get("icon", "")

    city_name = data.get("name") or "Current Location"
    country = data.get("sys", {}).get("country", "")
    city_label = f"{city_name}, {country}" if country else city_name
    icao = _resolve_icao(city_name)
    metar, taf, metar_time, taf_valid = _fetch_metar_taf(icao)
    timezone_offset = int(data.get("timezone", 0))
    observation_ts = int(data.get("dt", int(datetime.utcnow().timestamp())))

    payload = {
        "city": city_label,
        "condition": data["weather"][0].get("description", "Unknown").title(),
        "temp": round(temp),
        "hi": round(float(data["main"].get("temp_max", temp))),
        "lo": round(float(data["main"].get("temp_min", temp))),
        "humidity": int(data["main"].get("humidity", 0)),
        "pressure": int(data["main"].get("pressure", 0)),
        "rain": round(rain, 1),
        "wind": wind_speed,
        "gust": wind_gust,
        "windDir": _wind_direction(wind_deg),
        "icon": _icon_to_emoji(icon_id),
        "icon_url": f"https://openweathermap.org/img/w/{icon_id}.png",
        "updated_time": datetime.now().strftime("%A, %B %d · %I:%M %p"),
        "timezoneOffset": timezone_offset,
        "obsTs": observation_ts,
        "hourlyTemps": hourly_temps,
        "hourlyRain": hourly_rain,
        "metar": metar,
        "taf": taf,
        "metarTime": metar_time,
        "tafValid": taf_valid,
        "alert": rain >= 4,
        "alertMsg": (
            "Heavy rain expected. Plan travel with caution." if rain >= 4 else ""
        ),
    }
    return payload, None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/weather", methods=["POST"])
def api_weather():
    city = ""
    lat = None
    lon = None
    if request.is_json:
        body = request.get_json(silent=True) or {}
        city = (body.get("city") or "").strip()
        lat = body.get("lat")
        lon = body.get("lon")
    else:
        city = (request.form.get("city") or "").strip()
        lat = request.form.get("lat")
        lon = request.form.get("lon")

    has_coords = (
        lat is not None and lon is not None and str(lat) != "" and str(lon) != ""
    )
    if not city and not has_coords:
        return jsonify({"error": "Please enter a city name."}), 400

    try:
        if has_coords:
            weather_payload, error = _fetch_weather_by_coords(float(lat), float(lon))
        else:
            weather_payload, error = _fetch_weather(city)
    except requests.RequestException:
        return (
            jsonify({"error": "Could not reach weather service. Please try again."}),
            503,
        )
    except ValueError:
        return jsonify({"error": "Invalid location coordinates."}), 400

    if error:
        return jsonify({"error": error}), 404

    return jsonify(weather_payload)


@app.route("/weather", methods=["POST"])
def weather():
    city = (request.form.get("city") or "").strip()
    if not city:
        return render_template("error.html", error="Please enter a city name.")

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={city}&appid={API_KEY}&units=metric"
    )

    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except requests.RequestException:
        return render_template(
            "error.html", error="Could not reach weather service. Please try again."
        )

    # OpenWeather may return cod as int or string, so normalize before checking.
    cod = str(data.get("cod", ""))
    if cod == "200":
        weather_desc = data["weather"][0]["description"].title()
        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        timestamp = data.get("dt")
        formatted_time = (
            datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            if timestamp
            else "N/A"
        )

        icon_id = data["weather"][0]["icon"]
        icon_url = f"https://openweathermap.org/img/w/{icon_id}.png"

        return render_template(
            "weather.html",
            city=city.title(),
            time=formatted_time,
            weather=weather_desc,
            temp=temp,
            feels_like=feels_like,
            humidity=humidity,
            icon_url=icon_url,
        )

    error = data.get("message", "Could not fetch weather for that city.")
    return render_template("error.html", error=error)


if __name__ == "__main__":
    app.run(debug=True)
