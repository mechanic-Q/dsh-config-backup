import os, sys, json, collections
sys.path.insert(0, 'scripts')
import rotation_dataset as rd, rotation_groundtruth as gt
rows = rd.load()
buckets = gt.evidence_by_row(rows)
for r in [x for x in rows if x.get('user_flagged') is True]:
    ents = buckets.get((r['date'], r['slug'], r['shot']), [])
    if ents:
        for e in ents:
            actual = rd.sha256_file(e['frame']) if e['linked'] else ''
            print(r['date'], r['shot'], e['category'], e['round'], e['source'],
                  'MATCH' if actual and actual == e['sha256'] else ('NO-SHA' if not e['sha256'] else 'MISMATCH'))
    # work frame listing
# also: how many t15-evidence dirs
import glob
print('t15 dirs', len(glob.glob('references/t15-evidence-*')))
print('prod manifests', len(rd.iter_production_evidence_manifests()))
