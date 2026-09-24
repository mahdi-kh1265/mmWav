import sys, os
sys.path.insert(0, r"C:\Users\khams008\Documents\awr2944-fmcw-radar\src")
os.chdir(r"C:\Users\khams008\Documents\saeed_radar_acceptance")

from awr2944_dca import RadarProject

p = RadarProject.open(".")

print("=" * 60)
print("DCA aliveness check")
print("=" * 60)

v = p.dca.verify()
print(f"success = {v.success}")
print(f"detail  = {v.sys_status_detail}")

if not v.success:
    raise SystemExit("DCA not responding — stopping before capture.")

print()
print("=" * 60)
print("REAL SMOKE CAPTURE")
print("=" * 60)

result = p.capture.run_smoke(
    name="acceptance_smoke_retry",
    frames=8,
    guard_frames=1,
)

print()
print("RESULT")
print(f"success = {result.success}")
print(result)
