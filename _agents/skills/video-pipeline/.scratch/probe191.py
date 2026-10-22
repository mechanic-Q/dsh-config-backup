import os, sys, json, collections
sys.path.insert(0, 'scripts')
import rotation_dataset as rd, rotation_groundtruth as gt
rows = rd.load()
print('rows', len(rows))
fl = [r for r in rows if r.get('user_flagged') is True]
print('flagged True', len(fl))
print('with evidence_frames', sum(1 for r in fl if r.get('evidence_frames')))
print('with frame_stale', sum(1 for r in rows if r.get('frame_stale')))
print('date_root exists', os.path.isdir(gt.DATE_ROOT), gt.DATE_ROOT)
buckets = gt.evidence_by_row(rows)
print('bucket keys', len(buckets))
cc = collections.Counter()
for r in fl:
    k = (r['date'], r['slug'], r['shot'])
    cc['work_ok' if gt.frames_of(r) else 'work_missing'] += 1
    ents = buckets.get(k, [])
    if ents:
        cc['bucket_has'] += 1
        cc['bucket_linked' if any(e['linked'] for e in ents) else 'bucket_unlinked'] += 1
    else:
        cc['bucket_none'] += 1
    if r.get('evidence_frames'):
        cc['row_ev'] += 1
print(dict(cc))
for r in fl[:5]:
    print(r['date'], r['slug'], r['shot'], len(r.get('evidence_frames') or []),
          len(buckets.get((r['date'],r['slug'],r['shot']), [])), r.get('frame_stale'))
