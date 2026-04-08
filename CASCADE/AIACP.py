import os
import sys
import csv
import json
import platform
import subprocess
import datetime
import argparse

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed.\nRun: pip install psutil")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests not installed.\nRun: pip install requests")
    sys.exit(1)

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("NOTE: pandas not installed. Local CSV export will use built-in csv module.\n"
          "Run: pip install pandas  (optional)")

EPD_DATABASE_KG_CO2E = {
    "macbookair10,1": 147,
    "macbookair10,2": 147,
    "macbookair14,2": 147,
    "macbookpro17,1": 185,
    "macbookpro18,1": 349,
    "macbookpro18,3": 504,
    "latitude 5540":  381,
    "latitude 7440":  354,
    "latitude 5440":  370,
    "inspiron 15 3520": 320,
    "thinkpad x1 carbon gen 11": 310,
    "thinkpad t14s gen 4":       350,
    "thinkpad e15 gen 4":        380,
    "elitebook 840 g10":  390,
    "probook 450 g10":    370,
    "__default__": 350,
}

BASELINE_LIFESPAN_YEARS = 4

def _lookup_epd(model_name: str) -> tuple[int, str]:
    if model_name:
        lower_model = model_name.lower().strip()
        for key, value in EPD_DATABASE_KG_CO2E.items():
            if key == "__default__":
                continue
            if key in lower_model or lower_model in key:
                return value, f"EPD lookup ({model_name})"
    return EPD_DATABASE_KG_CO2E["__default__"], "Fraunhofer IZM average (350 kg — no model-specific EPD found)"

def _get_device_model() -> str:
    try:
        if platform.system() == "Linux":
            for path in ["/sys/class/dmi/id/product_name",
                         "/sys/class/dmi/id/board_name"]:
                if os.path.exists(path):
                    with open(path) as f:
                        return f.read().strip()
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "Model Identifier" in line:
                    return line.split(":")[-1].strip().lower()
        elif platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "computersystem", "get", "model"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if len(lines) > 1:
                return lines[1].lower()
    except Exception:
        pass
    return ""

def run_dejunking():
    flags = []
    bloat_score = 0
    process_count = len(psutil.pids())

    if process_count > 250:
        flags.append(f"HIGH process count: {process_count} running processes (>250 threshold)")
        bloat_score += 20
    elif process_count > 150:
        flags.append(f"MODERATE process count: {process_count} running processes")
        bloat_score += 10

    swap = psutil.swap_memory()
    if swap.percent > 60:
        flags.append(f"HIGH swap usage: {swap.percent:.1f}% (RAM under severe pressure)")
        bloat_score += 25
    elif swap.percent > 30:
        flags.append(f"MODERATE swap usage: {swap.percent:.1f}%")
        bloat_score += 10

    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            if usage.percent > 90:
                flags.append(f"CRITICAL disk fullness: {part.mountpoint} is {usage.percent:.1f}% full")
                bloat_score += 20
            elif usage.percent > 75:
                flags.append(f"HIGH disk usage: {part.mountpoint} is {usage.percent:.1f}% full")
                bloat_score += 10
        except (PermissionError, OSError):
            pass

    high_impact_procs = []
    try:
        all_procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                if info["cpu_percent"] and info["cpu_percent"] > 5.0:
                    all_procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        high_impact_procs = sorted(all_procs, key=lambda x: x["cpu_percent"], reverse=True)[:5]
        if high_impact_procs:
            flags.append("HIGH-IMPACT PROCESSES detected (recommend manual review):")
            for p in high_impact_procs:
                flags.append(
                    f"    PID {p['pid']:>6} | {p['name']:<30} | "
                    f"CPU: {p['cpu_percent']:.1f}%  RAM: {p['memory_percent']:.1f}%"
                )
            bloat_score += min(20, len(high_impact_procs) * 4)
    except Exception:
        pass

    cleared_mb = 0
    temp_dirs = []
    if platform.system() == "Windows":
        temp_dirs = [os.environ.get("TEMP", ""), os.environ.get("TMP", "")]
    elif platform.system() in ["Linux", "Darwin"]:
        temp_dirs = ["/tmp"]

    for temp_dir in temp_dirs:
        if temp_dir and os.path.exists(temp_dir):
            for f in os.listdir(temp_dir):
                fp = os.path.join(temp_dir, f)
                try:
                    if os.path.isfile(fp):
                        size = os.path.getsize(fp)
                        os.remove(fp)
                        cleared_mb += size / (1024 * 1024)
                except (PermissionError, OSError):
                    pass

    bloat_score = min(100, bloat_score)
    return flags, bloat_score, round(cleared_mb, 2), high_impact_procs

def _stress_worker_process():
    x = 0.0
    import time
    end = time.time() + 3
    while time.time() < end:
        x += 1.1234567890 * 9.9999999999
        x = x % 1_000_000

def _cpu_stress_worker(duration_seconds=3):
    import multiprocessing
    import time

    num_cores = psutil.cpu_count(logical=True) or 1
    processes = [
        multiprocessing.Process(target=_stress_worker_process)
        for _ in range(num_cores)
    ]
    for p in processes:
        p.start()

    time.sleep(duration_seconds)

    for p in processes:
        p.terminate()
        p.join(timeout=2)

def extract_cpu_telemetry():
    import time

    baseline_usage = psutil.cpu_percent(interval=1)
    freq_before = None
    try:
        freq_info = psutil.cpu_freq(percpu=False)
        if freq_info:
            freq_before = freq_info.current
    except Exception:
        pass

    temp_before = {}
    try:
        sensor_data = psutil.sensors_temperatures()
        if sensor_data:
            for chip, entries in sensor_data.items():
                for entry in entries:
                    if entry.current and entry.current > 0:
                        temp_before[f"{chip}/{entry.label or 'core'}"] = entry.current
    except AttributeError:
        pass

    print("  [CPU] Applying 3-second stress load to detect throttling...")
    _cpu_stress_worker(duration_seconds=3)

    stressed_usage = psutil.cpu_percent(interval=1)
    freq_after = None
    try:
        freq_info = psutil.cpu_freq(percpu=False)
        if freq_info:
            freq_after = freq_info.current
    except Exception:
        pass

    temp_after = {}
    try:
        sensor_data = psutil.sensors_temperatures()
        if sensor_data:
            for chip, entries in sensor_data.items():
                for entry in entries:
                    if entry.current and entry.current > 0:
                        temp_after[f"{chip}/{entry.label or 'core'}"] = entry.current
    except AttributeError:
        pass

    if not temp_after and platform.system() == "Windows":
        try:
            import wmi
            w = wmi.WMI(namespace="root\\wmi")
            for t in w.MSAcpi_ThermalZoneTemperature():
                celsius = (t.CurrentTemperature / 10.0) - 273.15
                temp_after["ACPI/ThermalZone"] = round(celsius, 1)
        except Exception:
            pass

    throttling_detected = False
    throttling_reason = []
    freq_drop_pct = None

    if freq_before and freq_after:
        if freq_after < freq_before * 0.95:
            throttling_detected = True
            freq_drop_pct = round((1 - freq_after / freq_before) * 100, 1)
            throttling_reason.append(f"Clock speed dropped {freq_drop_pct}% under load")

    max_temp_after = max(temp_after.values()) if temp_after else 0
    if max_temp_after > 85:
        throttling_detected = True
        throttling_reason.append(f"Thermal threshold exceeded: {max_temp_after}°C")
    elif max_temp_after > 75:
        throttling_reason.append(f"Thermal warning: {max_temp_after}°C")

    avg_usage = (baseline_usage + stressed_usage) / 2
    usage_penalty = round(avg_usage * 0.5, 1)
    throttle_penalty = 25 if throttling_detected else 0
    freq_penalty = min(15, round((freq_drop_pct or 0) * 0.5))
    cpu_health = max(0, round(100 - usage_penalty - throttle_penalty - freq_penalty, 1))

    return {
        "health_score": cpu_health,
        "avg_usage_pct": round(avg_usage, 1),
        "baseline_usage_pct": round(baseline_usage, 1),
        "stressed_usage_pct": round(stressed_usage, 1),
        "freq_before_mhz": round(freq_before, 1) if freq_before else None,
        "freq_after_mhz": round(freq_after, 1) if freq_after else None,
        "freq_drop_pct": freq_drop_pct,
        "throttling_detected": throttling_detected,
        "throttling_reasons": throttling_reason,
        "temp_before_c": temp_before,
        "temp_after_c": temp_after,
        "max_temp_c": max_temp_after if max_temp_after else None,
    }

def extract_battery_telemetry():
    bat = psutil.sensors_battery()
    wear_pct = None
    design_capacity = None
    full_charge_capacity = None
    method_used = "none"

    if platform.system() == "Linux":
        for bat_path in ["/sys/class/power_supply/BAT0",
                         "/sys/class/power_supply/BAT1"]:
            try:
                with open(f"{bat_path}/energy_full_design") as f:
                    design_capacity = int(f.read().strip())
                with open(f"{bat_path}/energy_full") as f:
                    full_charge_capacity = int(f.read().strip())
                wear_pct = round((1 - full_charge_capacity / design_capacity) * 100, 1)
                method_used = "Linux sysfs (kernel.org power_supply_class)"
                break
            except FileNotFoundError:
                pass

    elif platform.system() == "Windows":
        try:
            report_path = os.path.join(
                os.environ.get("TEMP", "C:\\Temp"), "battery_report.xml"
            )
            subprocess.run(
                ["powercfg", "/batteryreport", "/xml", "/output", report_path],
                capture_output=True, timeout=15
            )
            if os.path.exists(report_path):
                import xml.etree.ElementTree as ET
                tree = ET.parse(report_path)
                root = tree.getroot()

                design = None
                full = None

                for battery in root.iter("Battery"):
                    design = battery.find("DesignCapacity")
                    full   = battery.find("FullChargeCapacity")
                    if design is not None and full is not None:
                        break

                if design is None or full is None:
                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                        if tag == "DesignCapacity" and elem.text:
                            design = elem
                        if tag == "FullChargeCapacity" and elem.text:
                            full = elem
                        if design is not None and full is not None:
                            break

                if design is not None and full is not None:
                    design_capacity = int(design.text)
                    full_charge_capacity = int(full.text)
                    wear_pct = round(
                        (1 - full_charge_capacity / design_capacity) * 100, 1
                    )
                    method_used = "Windows powercfg /batteryreport (namespace-aware)"
                else:
                    method_used = "Windows powercfg — XML tag not found (schema mismatch)"
        except Exception as e:
            method_used = f"Windows powercfg failed: {e}"

    if bat is None and wear_pct is None:
        return {
            "health_score": 100,
            "wear_pct": None,
            "design_capacity": None,
            "full_charge_capacity": None,
            "current_charge_pct": None,
            "is_desktop": True,
            "method": "No battery detected — desktop or battery driver absent",
            "data_quality": "not_applicable",
        }

    if wear_pct is None:
        return {
            "health_score": None,
            "wear_pct": None,
            "design_capacity": None,
            "full_charge_capacity": None,
            "current_charge_pct": bat.percent if bat else None,
            "is_desktop": False,
            "method": method_used,
            "data_quality": "unavailable — real capacity data could not be read; "
                            "psutil SoC proxy removed (scientifically invalid per "
                            "EU Battery Regulation 2023/1542)",
        }

    health_score = max(0, round(100 - wear_pct, 1))
    return {
        "health_score": health_score,
        "wear_pct": wear_pct,
        "design_capacity": design_capacity,
        "full_charge_capacity": full_charge_capacity,
        "current_charge_pct": bat.percent if bat else None,
        "is_desktop": False,
        "method": method_used,
        "data_quality": "verified",
    }

SSD_WEAR_ATTRIBUTES = {
    "SSD_Life_Left":              "life_remaining",
    "Media_Wearout_Indicator":    "life_remaining",
    "Wear_Leveling_Count":        "wear_count",
    "Avg_Erase_Cnt_Tot":          "wear_count",
    "Total_Erase_Count":          "wear_count",
    "Available_Reservd_Space":    "life_remaining",
    "Initial_Bad_Block_Count":    "wear_count",
    "Wear_Range_Delta":           "wear_count",
    "Runtime_Bad_Block":          "wear_count",
}

def _parse_smart_attribute(lines, attribute_name):
    for line in lines:
        if attribute_name in line:
            parts = line.split()
            if len(parts) >= 10:
                try:
                    return int(parts[9])
                except ValueError:
                    pass
    return None

def _detect_drive_type(smartctl_output):
    output_lower = smartctl_output.lower()
    if "solid state" in output_lower or "ssd" in output_lower or "nvme" in output_lower:
        return "SSD"
    if "rotation rate" in output_lower:
        return "HDD"
    return "Unknown"

def _find_disk_devices():
    if platform.system() == "Linux":
        return ["/dev/sda", "/dev/sdb", "/dev/nvme0", "/dev/nvme0n1", "/dev/hda"]
    elif platform.system() == "Darwin":
        return ["/dev/disk0", "/dev/disk1"]
    elif platform.system() == "Windows":
        return ["/dev/sda", "/dev/pd0"]
    return ["/dev/sda"]

def extract_disk_telemetry():
    smart_available = False
    smart_status = "Unknown"
    drive_type = "Unknown"
    smart_attrs = {}
    smart_warnings = []
    penalty = 0

    devices = _find_disk_devices()
    for device in devices:
        try:
            result = subprocess.run(
                ["smartctl", "-H", "-A", "-i", device],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout
            lines = output.splitlines()

            if not output.strip():
                continue

            smart_available = True
            drive_type = _detect_drive_type(output)

            if "PASSED" in output:
                smart_status = "PASSED"
            elif "FAILED" in output:
                smart_status = "FAILED"
                penalty += 60
                smart_warnings.append("CRITICAL: Overall S.M.A.R.T. health test FAILED")

            power_on_hours = _parse_smart_attribute(lines, "Power_On_Hours")
            temperature = _parse_smart_attribute(lines, "Temperature_Celsius")
            if temperature is None:
                temperature = _parse_smart_attribute(lines, "Airflow_Temperature_Cel")

            if power_on_hours:
                smart_attrs["power_on_hours"] = power_on_hours
                if power_on_hours > 35000:
                    penalty += 15
                    smart_warnings.append(
                        f"High usage: {power_on_hours:,} hours "
                        f"(~{power_on_hours//8760} years continuous)"
                    )
                elif power_on_hours > 20000:
                    penalty += 7

            if temperature:
                smart_attrs["temperature_c"] = temperature
                if temperature > 55:
                    penalty += 10
                    smart_warnings.append(f"Drive temperature high: {temperature}°C")

            if drive_type in ("HDD", "Unknown"):
                reallocated = _parse_smart_attribute(lines, "Reallocated_Sector_Ct")
                pending = _parse_smart_attribute(lines, "Current_Pending_Sector")

                if reallocated is not None:
                    smart_attrs["reallocated_sectors"] = reallocated
                    if reallocated > 50:
                        penalty += 25
                        smart_warnings.append(
                            f"CRITICAL: {reallocated} reallocated sectors "
                            f"(spare pool nearly exhausted)"
                        )
                    elif reallocated > 0:
                        penalty += 10
                        smart_warnings.append(
                            f"WARNING: {reallocated} reallocated sectors detected"
                        )

                if pending is not None:
                    smart_attrs["pending_sectors"] = pending
                    if pending > 0:
                        penalty += 15
                        smart_warnings.append(
                            f"WARNING: {pending} sectors pending reallocation "
                            f"(active read errors)"
                        )

            if drive_type in ("SSD", "Unknown"):
                ssd_wear_found = False
                for attr_name, attr_type in SSD_WEAR_ATTRIBUTES.items():
                    raw_val = _parse_smart_attribute(lines, attr_name)
                    if raw_val is not None:
                        if attr_type == "life_remaining":
                            life_left_pct = raw_val
                            smart_attrs["ssd_life_left_pct"] = life_left_pct
                            smart_attrs["ssd_wear_source"] = attr_name
                            if life_left_pct < 20:
                                penalty += 20
                                smart_warnings.append(
                                    f"CRITICAL SSD wear: only {life_left_pct}% life "
                                    f"remaining ({attr_name})"
                                )
                            elif life_left_pct < 50:
                                penalty += 10
                                smart_warnings.append(
                                    f"SSD wear: {life_left_pct}% life remaining "
                                    f"({attr_name})"
                                )
                        elif attr_type == "wear_count":
                            smart_attrs["ssd_wear_count"] = raw_val
                            smart_attrs["ssd_wear_source"] = attr_name
                            if raw_val > 80:
                                penalty += 20
                                smart_warnings.append(
                                    f"CRITICAL SSD wear count: {raw_val} ({attr_name})"
                                )
                            elif raw_val > 50:
                                penalty += 10
                        ssd_wear_found = True
                        break

                if not ssd_wear_found:
                    smart_warnings.append(
                        "SSD wear attribute not found — vendor not in database "
                        "(checked: SSD_Life_Left, Wear_Leveling_Count, "
                        "Available_Reservd_Space, Avg_Erase_Cnt_Tot, others)"
                    )

            break

        except Exception:
            continue

    disk_usage_data = []
    worst_used_pct = 0
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disk_usage_data.append({
                "mountpoint": part.mountpoint,
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_pct": round(usage.percent, 1),
            })
            if usage.percent > worst_used_pct:
                worst_used_pct = usage.percent
        except Exception:
            pass

    if smart_available:
        disk_health = max(0, 100 - min(penalty, 90))
    else:
        disk_health = max(0, round(100 - worst_used_pct))
        smart_warnings.append(
            "smartmontools not installed — disk health estimated from "
            "partition fullness only (software proxy, not hardware data)"
        )

    return {
        "health_score": round(disk_health, 1),
        "smart_available": smart_available,
        "smart_status": smart_status,
        "drive_type": drive_type,
        "smart_attrs": smart_attrs,
        "smart_warnings": smart_warnings,
        "partitions": disk_usage_data,
        "worst_used_pct": worst_used_pct,
    }

def compute_hhs(cpu_health, disk_health, battery_health, bloat_score,
                battery_is_desktop=False):
    battery_available = (
        battery_health is not None
        and not battery_is_desktop
    )

    if battery_available:
        hhs_raw = (cpu_health * 0.4) + (disk_health * 0.4) + (battery_health * 0.2)
        formula_str = (
            f"({cpu_health} × 0.4) + ({disk_health} × 0.4) + "
            f"({battery_health} × 0.2)"
        )
    else:
        hhs_raw = (cpu_health * 0.5) + (disk_health * 0.5)
        formula_str = (
            f"({cpu_health} × 0.5) + ({disk_health} × 0.5) "
            f"[battery excluded — data unavailable or desktop]"
        )

    bloat_penalty = round((bloat_score / 100) * 10, 1)
    hhs_final = max(0, round(hhs_raw - bloat_penalty, 1))

    return {
        "hhs": hhs_final,
        "hhs_raw": round(hhs_raw, 1),
        "bloat_penalty": bloat_penalty,
        "battery_included": battery_available,
        "formula": formula_str + f" - {bloat_penalty} (bloat) = {hhs_final}",
    }

def cascading_decision(hhs):
    if hhs > 80:
        return {
            "tier": 1,
            "label": "Status Quo",
            "action": "Retain for high-compute workloads.",
            "battery_extraction": False,
            "years_extended": 3,
            "tier_basis": "DESIGN CHOICE: HHS > 80 — all subsystems healthy",
        }
    elif hhs >= 50:
        return {
            "tier": 2,
            "label": "Light Duty",
            "action": "Reassign to library terminal or low-intensity workloads.",
            "battery_extraction": False,
            "years_extended": 3,
            "tier_basis": "DESIGN CHOICE: HHS 50–80 — degraded but operational",
        }
    elif hhs >= 20:
        return {
            "tier": 3,
            "label": "Stationary Operations",
            "action": "Wall-power the device; remove battery if degraded.",
            "battery_extraction": True,
            "years_extended": 2,
            "tier_basis": "DESIGN CHOICE: HHS 20–50 — suitable for signage/kiosk only",
        }
    else:
        return {
            "tier": 4,
            "label": "E-Waste Routing",
            "action": "Route to certified e-waste recycler.",
            "battery_extraction": False,
            "years_extended": 0,
            "tier_basis": "DESIGN CHOICE: HHS < 20 — impractical to continue operation",
        }

def recommend_os(tier, ram_gb):
    if tier == 1:
        return {
            "recommendation": "Current OS suitable.",
            "distro": None,
            "rationale": None,
        }
    elif tier == 2:
        if ram_gb >= 8:
            distro   = "Linux Mint (XFCE)"
            rationale = "Recommended ≥8 GB RAM. SOURCE: linuxmint.com"
        elif ram_gb >= 4:
            distro   = "Lubuntu"
            rationale = "Optimised for 4–8 GB RAM. SOURCE: lubuntu.me"
        else:
            distro   = "Puppy Linux"
            rationale = "Runs in <512 MB RAM. SOURCE: puppylinux.com"
        return {
            "recommendation": f"Transition to {distro}.",
            "distro": distro,
            "rationale": rationale,
        }
    elif tier == 3:
        return {
            "recommendation": "Lubuntu or DietPi.",
            "distro": "Lubuntu / DietPi",
            "rationale": "Minimal resource usage for kiosk/signage. SOURCE: lubuntu.me",
        }
    else:
        return {
            "recommendation": "No OS intervention — route to e-waste.",
            "distro": None,
            "rationale": None,
        }

def calculate_embodied_carbon(years_extended, device_model=""):
    if years_extended == 0:
        return {
            "carbon_saved_kg": 0,
            "baseline_carbon_kg": 0,
            "carbon_source": "N/A — device routed to e-waste",
            "formula_used": "No extension",
            "scope": "Scope 3",
        }

    baseline_carbon_kg, carbon_source = _lookup_epd(device_model)

    carbon_per_year = baseline_carbon_kg / BASELINE_LIFESPAN_YEARS
    carbon_saved = round(carbon_per_year * years_extended, 1)

    return {
        "carbon_saved_kg": carbon_saved,
        "baseline_carbon_kg": baseline_carbon_kg,
        "carbon_source": carbon_source,
        "formula_used": (
            f"({baseline_carbon_kg} kg / {BASELINE_LIFESPAN_YEARS} yr) "
            f"× {years_extended} yr = {carbon_saved} kg CO2e"
        ),
        "scope": "Scope 3 (GHG Protocol, Category 2 — avoided procurement emissions)",
    }

def _build_flat_dict(report_data):
    return {
        "timestamp":                  report_data["timestamp"],
        "hostname":                   platform.node(),
        "device_model":               report_data["device_model"],
        "device_os":                  report_data["system"]["os"],
        "cpu_health_score":           report_data["cpu"]["health_score"],
        "cpu_baseline_usage_pct":     report_data["cpu"]["baseline_usage_pct"],
        "cpu_stressed_usage_pct":     report_data["cpu"]["stressed_usage_pct"],
        "cpu_freq_before_mhz":        report_data["cpu"]["freq_before_mhz"],
        "cpu_freq_after_mhz":         report_data["cpu"]["freq_after_mhz"],
        "cpu_freq_drop_pct":          report_data["cpu"]["freq_drop_pct"],
        "cpu_throttling":             report_data["cpu"]["throttling_detected"],
        "battery_health_score":       report_data["battery"]["health_score"],
        "battery_wear_pct":           report_data["battery"]["wear_pct"],
        "battery_data_quality":       report_data["battery"].get("data_quality", "unknown"),
        "battery_method":             report_data["battery"].get("method", ""),
        "disk_health_score":          report_data["disk"]["health_score"],
        "disk_smart_status":          report_data["disk"]["smart_status"],
        "disk_drive_type":            report_data["disk"]["drive_type"],
        "disk_power_on_hours":        report_data["disk"]["smart_attrs"].get("power_on_hours"),
        "disk_reallocated_sectors":   report_data["disk"]["smart_attrs"].get("reallocated_sectors"),
        "disk_pending_sectors":       report_data["disk"]["smart_attrs"].get("pending_sectors"),
        "disk_ssd_life_left_pct":     report_data["disk"]["smart_attrs"].get("ssd_life_left_pct"),
        "disk_ssd_wear_source":       report_data["disk"]["smart_attrs"].get("ssd_wear_source"),
        "disk_temperature_c":         report_data["disk"]["smart_attrs"].get("temperature_c"),
        "ram_gb":                     report_data["ram_gb"],
        "bloat_score":                report_data["dejunk"]["bloat_score"],
        "temp_cleared_mb":            report_data["dejunk"]["cleared_mb"],
        "hhs_raw":                    report_data["hhs"]["hhs_raw"],
        "hhs_final":                  report_data["hhs"]["hhs"],
        "hhs_battery_included":       report_data["hhs"]["battery_included"],
        "tier":                       report_data["decision"]["tier"],
        "tier_label":                 report_data["decision"]["label"],
        "battery_extraction_required":report_data["decision"]["battery_extraction"],
        "years_extended":             report_data["decision"]["years_extended"],
        "os_recommendation":          report_data["os_rec"]["distro"],
        "carbon_saved_kg_co2e":       report_data["carbon"]["carbon_saved_kg"],
        "carbon_baseline_kg":         report_data["carbon"]["baseline_carbon_kg"],
        "carbon_source":              report_data["carbon"]["carbon_source"],
    }

def export_to_csv(report_data, filename=None):
    if filename is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"aiacp_report_{ts}.csv"

    flat = _build_flat_dict(report_data)

    if PANDAS_AVAILABLE:
        df = pd.DataFrame([flat])
        df.to_csv(filename, index=False)
    else:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=flat.keys())
            writer.writeheader()
            writer.writerow(flat)
    return filename

def export_to_cloud(report_data, webhook_url):
    flat_dict = _build_flat_dict(report_data)
    row_array = list(flat_dict.values())
    payload = {"values": [row_array]}

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"  ✓ SUCCESS: {len(row_array)} columns sent to Google Sheets.")
            return True
        else:
            print(f"  ✗ Cloud API Error: Status {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        print(f"  ✗ Network connection failed: {e}")
        return False

def run_protocol():
    print("\n" + "═" * 65)
    print("  ALGORITHMIC IT ASSET CASCADING PROTOCOL (AIACP)")
    print("  SASTRA Deemed University — CCTE Project")
    print(f"  Run timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 65)

    device_model = _get_device_model()
    if device_model:
        print(f"\n  Device model detected: {device_model}")
    else:
        print("\n  Device model: not detected — will use Fraunhofer IZM average")

    print("\n[STAGE 0] Automated De-Junking Trigger...")
    junk_flags, bloat_score, cleared_mb, high_impact_procs = run_dejunking()
    print(f"  Bloat score  : {bloat_score}/100")
    print(f"  Temp cleared : {cleared_mb} MB")

    print("\n[STAGE 1] Telemetry Extraction Module...")
    cpu = extract_cpu_telemetry()
    battery = extract_battery_telemetry()
    disk = extract_disk_telemetry()
    ram = psutil.virtual_memory()
    ram_gb = round(ram.total / (1024 ** 3), 1)

    bat_health_display = (
        f"{battery['health_score']}/100"
        if battery["health_score"] is not None
        else f"UNAVAILABLE ({battery.get('data_quality', '')})"
    )
    print(f"  CPU health     : {cpu['health_score']}/100")
    print(f"  Battery health : {bat_health_display}")
    print(f"  Disk health    : {disk['health_score']}/100")
    print(f"  RAM            : {ram_gb} GB total")
    if battery.get("data_quality") == "unavailable":
        print(
            "  ⚠ Battery: real capacity data unavailable. "
            "HHS formula will use CPU + disk only (CHANGE 2)."
        )

    print("\n[STAGE 2] Weighted Grading Algorithm...")
    hhs_result = compute_hhs(
        cpu["health_score"],
        disk["health_score"],
        battery["health_score"],
        bloat_score,
        battery_is_desktop=battery.get("is_desktop", False),
    )
    print(f"  ► HARDWARE HEALTH SCORE (HHS) : {hhs_result['hhs']} / 100")
    print(f"    Formula: {hhs_result['formula']}")

    print("\n[STAGE 3] Cascading Decision Matrix...")
    decision = cascading_decision(hhs_result["hhs"])
    print(f"  ► TIER {decision['tier']}: {decision['label']}")
    print(f"    Action: {decision['action']}")

    print("\n[STAGE 4] Lightweight OS Recommender...")
    os_rec = recommend_os(decision["tier"], ram_gb)
    print(f"  ► {os_rec['recommendation']}")

    print("\n[STAGE 5] Embodied Carbon Calculator...")
    carbon = calculate_embodied_carbon(decision["years_extended"], device_model)
    print(f"  ► AVOIDED EMISSIONS : {carbon['carbon_saved_kg']} kg CO2e")
    print(f"    Source            : {carbon['carbon_source']}")
    print(f"    Formula           : {carbon['formula_used']}")
    print(f"    Scope             : {carbon['scope']}")

    report = {
        "timestamp":    datetime.datetime.now().isoformat(),
        "device_model": device_model,
        "system":       {"os": platform.system(), "version": platform.version()},
        "dejunk":       {
            "bloat_score": bloat_score,
            "flags":       junk_flags,
            "cleared_mb":  cleared_mb,
            "high_impact_procs": high_impact_procs,
        },
        "cpu":      cpu,
        "battery":  battery,
        "disk":     disk,
        "ram_gb":   ram_gb,
        "hhs":      hhs_result,
        "decision": decision,
        "os_rec":   os_rec,
        "carbon":   carbon,
    }

    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AIACP — Algorithmic IT Asset Cascading Protocol"
    )
    parser.add_argument(
        "--cloud",
        metavar="URL",
        help="Google Apps Script Webhook URL to upload data automatically.",
        default=(
            "https://script.google.com/macros/s/"
            "AKfycbwG5QN6ztNvh3g3fyK4XaRfe8ZSjWoTAOMv6mzX1ToufDMPzyno_P0J145K7kkKb2Tf/exec"
        ),
    )
    args = parser.parse_args()

    report = run_protocol()

    if args.cloud:
        print("\n[CLOUD EXPORT] Initiating secure Webhook payload...")
        success = export_to_cloud(report, args.cloud)
        if not success:
            print("  Falling back to local CSV save...")
            export_to_csv(report)
    else:
        print("\n[LOCAL EXPORT] Generating ESG dashboard CSV...")
        csv_file = export_to_csv(report)
        print(f"  ► Saved: {csv_file}")
        print(
            "\n  TIP: To send to Google Sheet, run:\n"
            '  python aiacp_corrected.py --cloud "https://script.google.com/..."'
        )