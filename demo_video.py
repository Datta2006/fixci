#!/usr/bin/env python3
"""
Simple FixCI demo for video recording - no LLM calls, just simulated output.
"""
import sys
import time
from pathlib import Path

SCENARIOS = {
    "1": ("scenario1_bad_dependency", "Bad dependency (flask==0.0.1)"),
    "2": ("scenario2_lint", "Lint failures (unused import, unused variable)"),
    "3": ("scenario3_offbyone", "Off-by-one test failure"),
}

def print_step(text, delay=0.8):
    print(f"   {text}")
    time.sleep(delay)

def print_slow(text, delay=0.03):
    for char in text:
        print(char, end='', flush=True)
        time.sleep(delay)
    print()

def run_demo(scenario_key):
    name, desc = SCENARIOS[scenario_key]
    
    print(f"\n{'='*60}")
    print(f"  FixCI Demo: {name}")
    print(f"  {desc}")
    print(f"{'='*60}\n")
    
    print("📁 Setting up demo repository...")
    time.sleep(0.5)
    print(f"   Repo: /tmp/fixci_demo_{name}_xxxxxx")
    time.sleep(0.3)
    
    print("\n🔴 Running failing command to capture failure...")
    time.sleep(0.5)
    
    if scenario_key == "1":
        print("   Command: pip install -r requirements.txt")
        print("   Exit code: 1 (failed)")
        print("   Log preview:")
        print("   ERROR: Could not find a version that satisfies the requirement flask==0.0.1")
        print("   ERROR: No matching distribution found for flask==0.0.1")
    elif scenario_key == "2":
        print("   Command: ruff check .")
        print("   Exit code: 1 (failed)")
        print("   Log preview:")
        print("   F401 [*] `os` imported but unused")
        print("    --> app.py:5:8")
        print("   F841 Local variable `unused` assigned but never used")
        print("    --> app.py:10:5")
    else:
        print("   Command: pytest -q")
        print("   Exit code: 1 (failed)")
        print("   Log preview:")
        print("   FAILED test_offbyone.py::test_sum - assert 10 == 11")
    
    print("\n🔍 Analyzing failure...")
    time.sleep(0.5)
    if scenario_key == "1":
        print("   Classification: dependency_error")
    elif scenario_key == "2":
        print("   Classification: lint")
    else:
        print("   Classification: test_failure")
    
    print("\n🤖 Initializing FixCI pipeline...")
    time.sleep(0.3)
    
    print("\n🚀 Running FixCI pipeline...")
    print_step("   Step 1: Diagnosing failure with LLM...")
    print_step("   Step 2: Generating patch...")
    print_step("   Step 3: Verifying patch in Docker sandbox...")
    print_step("   Step 4: Creating fix PR (simulated)...")
    
    print(f"\n✅ Pipeline completed!")
    print(f"   Status: pr_opened")
    print(f"   Attempts: 1")
    
    if scenario_key == "1":
        print(f"   Root cause: requirements.txt pins flask==0.0.1 which does not exist on PyPI")
        print(f"   PR URL: https://github.com/demo/{name}/pull/42")
        print(f"   Fix: Changed flask==0.0.1 -> flask==3.0.0 in requirements.txt")
    elif scenario_key == "2":
        print(f"   Root cause: app.py has unused import 'os' and unused variable 'unused'")
        print(f"   PR URL: https://github.com/demo/{name}/pull/43")
        print(f"   Fix: Removed 'import os' and 'unused = ...' from app.py")
    else:
        print(f"   Root cause: Off-by-one error in sum_range function (uses < instead of <=)")
        print(f"   PR URL: https://github.com/demo/{name}/pull/44")
        print(f"   Fix: Changed 'for i in range(1, n)' to 'for i in range(1, n+1)'")
    
    print(f"\n📊 Recent runs:")
    print(f"   Run 12345: pr_opened (demo/{name})")
    print(f"   Run 12344: pr_opened (demo/scenario1_bad_dependency)")
    print(f"   Run 12343: diagnosis_only (demo/scenario3_offbyone)")
    
    print(f"\n🧹 Cleaning up temp directory...")
    time.sleep(0.3)
    
    print(f"\n{'='*60}")
    print("  Demo complete! FixCI opened a verified fix PR.")
    print(f"{'='*60}\n")

def main():
    if len(sys.argv) < 2:
        print("Usage: python demo_video.py <scenario>")
        print("\nAvailable scenarios:")
        for k, (name, desc) in SCENARIOS.items():
            print(f"  {k} - {name}: {desc}")
        print("\nExample: python demo_video.py 2")
        sys.exit(1)
    
    scenario = sys.argv[1]
    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}")
        print(f"Available: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)
    
    run_demo(scenario)

if __name__ == "__main__":
    main()