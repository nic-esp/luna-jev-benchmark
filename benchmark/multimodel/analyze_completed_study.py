#!/usr/bin/env python3
"""Analyze, draw and locally build an already verified completed study offline."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import analyze
import run_study
import verify_summary
import write_results
spec=importlib.util.spec_from_file_location('completed_verification_helper',ROOT/'verify_completed_study.py')
verification_helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(verification_helper)


def preflight(quality_dirs,timing_dir,cache_dir,models,verification_path):
    v=json.loads(Path(verification_path).read_text())
    if v.get('passed') is not True or v.get('status')!='passed':raise ValueError('A passed complete-study verification is required before analysis')
    paths={'quality':[str(Path(p).resolve()) for p in quality_dirs],'timing':str(Path(timing_dir).resolve()),'cache':str(Path(cache_dir).resolve())}
    if any(paths[k]!=v['input_paths'][k] for k in paths):raise ValueError('Explicit inputs differ from the passed verification')
    if list(models)!=v['models']:raise ValueError('Explicit model order differs from verified selected models')
    if verification_helper.sha(ROOT/'analysis-spec.json')!=v['analysis_spec_sha256']:raise ValueError('Frozen analysis specification changed')
    if verification_helper.sha(ROOT/'model-selection.json')!=v['selection_sha256']:raise ValueError('Selected model configurations changed')
    for p,evidence in v['evidence_after'].items():
        if verification_helper.snapshot(p)!=evidence:raise ValueError('Verified input changed: '+p)
    for p in list(quality_dirs)+[timing_dir,cache_dir]:
        if json.loads((Path(p)/'metadata.json').read_text()).get('status')!='complete':raise ValueError('All primary runs must be complete')
    return v


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--quality',nargs='+',type=Path,required=True);p.add_argument('--timing',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--models',nargs='+',required=True);p.add_argument('--verification',type=Path,default=ROOT/'verification.json');p.add_argument('--figure-python',type=Path,default=Path(sys.executable),help='Python runtime with Matplotlib and NumPy installed');a=p.parse_args()
    # The same nonblocking lock used by live protocols prevents accidental
    # heavy bootstrap/plot work during serial timing or cache measurements.
    with run_study.isolated_phase('offline_verified_analysis'):
        v=preflight(a.quality,a.timing,a.cache,a.models,a.verification)
        summary=analyze.analyze(a.quality,a.timing,[a.cache],models=a.models,output_dir=ROOT,bootstrap_samples=5000,seed=20261002)
        print(json.dumps({'stage':'analysis_complete','summary':str(ROOT/'summary.json'),'quality_attempts':summary['quality']['attempts']}),flush=True)
        summary_check=verify_summary.verify_summary(ROOT/'summary.json')
        if not summary_check['passed']:raise ValueError('Independent summary verification failed; inspect summary-verification.json')
        print(json.dumps({'stage':'independent_summary_verified','checked_values':summary_check['checked_values']}),flush=True)
        # Keep a virtual environment's executable path: resolving its symlink
        # would select the base interpreter without the plotting libraries.
        figure_python=a.figure_python.absolute()
        if not figure_python.exists():raise ValueError('The existing figure runtime is unavailable')
        subprocess.run([str(figure_python),str(ROOT/'figures.py'),'--summary',str(ROOT/'summary.json')],cwd=ROOT.parent,check=True)
        subprocess.run([sys.executable,str(ROOT/'build_report.py')],cwd=ROOT.parent,check=True)
        write_results.build()
        preflight(a.quality,a.timing,a.cache,a.models,a.verification)
        print(json.dumps({'stage':'local_report_complete','models':a.models,'report':str(ROOT.parent.parent/'comparison.html'),'model_calls':0}),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
