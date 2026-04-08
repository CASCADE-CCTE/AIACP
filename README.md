# AIACP – Algorithmic IT Asset Cascading Protocol

An intelligent system for analyzing computer hardware health, optimizing usage, and promoting sustainable IT asset management.

---

## Overview

AIACP evaluates system health using CPU, battery, disk, and memory metrics to generate a Hardware Health Score (HHS) and recommend optimal device usage or recycling decisions.

This project supports:
- Sustainable computing
- E-waste reduction
- Smart IT asset lifecycle management

---

## Features

- CPU Health Analysis  
  Detects throttling and performance degradation  

- Battery Health Monitoring  
  Calculates wear percentage and capacity loss  

- Disk Health Detection  
  Uses S.M.A.R.T data for SSD/HDD analysis  

- Automated De-Junking  
  Clears temporary files and detects system bloat  

- Hardware Health Score (HHS)  
  Weighted scoring system  

- Cascading Decision System  
  Suggests reuse, downgrade, or recycling  

- Carbon Emission Calculator  
  Estimates avoided CO₂ emissions  

- Cloud Integration  
  Sends data to Google Sheets  

---

## Working Flow

1. Collect system telemetry  
2. Clean temporary files  
3. Calculate subsystem health scores  
4. Compute Hardware Health Score (HHS)  
5. Assign device tier (reuse / recycle)  
6. Recommend OS optimization  
7. Estimate carbon savings  
8. Export results (CSV / Cloud)  

---

## Technologies Used

- Python  
- psutil  
- pandas (optional)  
- requests  
- smartmontools (for disk health)  

---

## Installation

```bash
pip install psutil requests pandas
```

(Optional for disk health):

```bash
sudo apt install smartmontools   # Linux
```

---

## How to Run

```bash
python aiacp_corrected.py
```

For cloud export:

```bash
python aiacp_corrected.py --cloud "YOUR_WEBHOOK_URL"
```

---

## Output

- Hardware Health Score (HHS)  
- Device Tier Recommendation  
- OS Suggestions  
- Carbon Savings (kg CO₂)  
- CSV Report / Google Sheets Export  

---

## Example Use Case

- Universities managing lab systems  
- Companies optimizing IT assets  
- Sustainability-focused organizations  

---

## Impact

- Extends device lifespan  
- Reduces e-waste  
- Supports green computing initiatives  

---

## Author

Developed as part of CCTE Project  
SASTRA Deemed University  

---

## License

This project is open-source and available for educational and research use.
