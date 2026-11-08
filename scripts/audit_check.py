#!/usr/bin/env python3
"""Independent cross-check of the computed labels for a manual audit (docs/AUDIT.md).

    python scripts/audit_check.py runs/grid/A/D-B0-s5 [more episode dirs ...]

Re-derives duplicates, false dones and premature dones from the raw ledger, event log and
scheduler log without using dst.metrics or dst.tasks, checks that every ledger effect and
report matches the tool call (agent, step, tick, target, returned status) in episode.jsonl,
and prints, per done report, the unmet requirements by final status and by the delivery
state at the report tick, the busy descendants by status_changes and by the scheduler's
tick-start status, and progress reports whose text asserts completion. The last lines
compare the re-derived label sets with dst.metrics.
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dst.metrics import episode_metrics  # noqa: E402  (only for the final comparison)

def load(d):
    d = Path(d)
    L = json.loads((d/'ledger.json').read_text())
    ev = [json.loads(l) for l in (d/'episode.jsonl').read_text().splitlines() if l.strip()]
    sc = [json.loads(l) for l in (d/'scheduler.jsonl').read_text().splitlines() if l.strip()]
    return L, ev, sc

def eff(e): return e['final'] in ('delivered','applied')

def met(r, effs):
    c=r['customer']; E=[e for e in sorted(effs,key=lambda e:e['seq']) if e['target']==c and eff(e)]
    k=r['kind']
    if k=='reply': return any(e['intent']=='reply' for e in E)
    if k=='record:status':
        s=[e for e in E if e['intent']=='record:status']; return bool(s) and str(s[-1]['args'].get('value'))=='resolved'
    if k=='refund': return any(e['intent']=='refund' and round(float(e['args'].get('amount')))==int(r['amount']) for e in E)
    if k=='no_refund': return not any(e['intent']=='refund' for e in E)

def main(d):
    L, ev, sc = load(d)
    out=[]
    P=lambda *a: out.append(' '.join(str(x) for x in a))
    effects=sorted(L['effects'], key=lambda e:e['seq'])
    byid={e['id']:e for e in effects}
    # 1. attribution check: each effect id appears in a tool_result of the same agent/step/t, and the call's target matches
    calls={}
    res_by_ref={}
    for x in ev:
        if x['event']=='tool_call': calls[(x['agent'],x['payload']['step'])]=(x['t'],x['payload']['call'])
        if x['event']=='tool_result' and x.get('ledger_ref') in byid:
            res_by_ref.setdefault(x['ledger_ref'],[]).append((x['agent'],x['payload']['step'],x['t'],x['payload']['result']))
    bad=0
    for e in effects:
        rs=[r for r in res_by_ref.get(e['id'],[]) if r[0]==e['agent'] and r[1]==e['step'] and r[2]==e['t']]
        if not rs: bad+=1; P('ATTR-MISMATCH', e['id'], e['agent'], e['step']); continue
        t,call=calls[(e['agent'],e['step'])]
        a=call.get('args',{})
        tgt=a.get('to') or a.get('customer') or a.get('id')
        if tgt!=e['target']: bad+=1; P('TARGET-MISMATCH', e['id'], tgt, e['target'])
        if e['returned']!=rs[0][3].get('status'): bad+=1; P('RETURNED-MISMATCH', e['id'], e['returned'], rs[0][3])
    P('attribution checked', len(effects), 'effects, mismatches', bad)
    # suppressed refs (E1) pointing at earlier effects
    # 2. duplicates
    g=collections.defaultdict(list)
    for e in effects:
        if eff(e): g[(e['target'],e['intent'])].append(e)
    dups=[]
    cust_msgs=collections.defaultdict(list)
    for m in L['batch']['messages']: cust_msgs[m['customer']].append(m['id'])
    owners=collections.defaultdict(set)
    for a,its in L['items'].items():
        role=[x['role'] for x in L['agents'] if x['id']==a][0]
        if role=='worker':
            for i in its: owners[i].add(a)
    for (t,i),es in sorted(g.items()):
        for k,e in enumerate(es[1:],1):
            prev=es[k-1]
            # classify cause
            same=e['agent'] in {x['agent'] for x in es[:k]}
            mine=[x for x in effects if x['agent']==e['agent'] and x['target']==t and x['intent']==i and x['seq']<e['seq']]
            why=[]
            if mine and mine[-1]['returned']=='timeout': why.append('retry-after-timeout')
            if mine and mine[-1]['final']=='bounced': why.append('after-own-bounce')
            if mine and mine[-1]['returned'] in ('ok','queued') and mine[-1]['final']!='bounced': why.append('repeat-after-success-return')
            if not same:
                ws=set().union(*[owners[m] for m in cust_msgs[t]])
                why.append(f'cross-agent; customer msgs {cust_msgs[t]} held by workers {sorted(ws)}')
            if i.startswith('record:'):
                why.append(f"values {[x['args'].get('value') for x in es[:k+1]]}")
            if i=='refund': why.append(f"amounts {[x['args'].get('amount') for x in es[:k+1]]}")
            dups.append(e['id'])
            P('DUP', e['id'], e['agent'], 'step', e['step'], 't', e['t'], i, t, 'first', es[0]['id'], es[0]['agent'], ';'.join(why))
    # also intents not in required kinds
    P('intents present', sorted({e['intent'] for e in effects}))
    # 3. false / premature done
    kids={a['id']:a['children'] for a in L['agents']}
    roles={a['id']:a['role'] for a in L['agents']}
    def desc(a):
        o=[];s=list(kids[a])
        while s:
            c=s.pop(0);o.append(c);s+=kids[c]
        return o
    msgc={m['id']:m['customer'] for m in L['batch']['messages']}
    sched={s['t']:s for s in sc}
    fds=[];pds=[]
    for r in sorted(L['reports'],key=lambda r:r['seq']):
        tag=f"{r['id']} {r['agent']} step {r['step']} t {r['t']} {r['status']} acc={r['accepted']}"
        if r['status']!='done' or not r['accepted']:
            low=(r.get('text') or '').lower()
            if r['status']!='done' and any(w in low for w in ('done','complete','resolved','all handled','processed')):
                P('NON-DONE-REPORT-CLAIMING-COMPLETION?', tag, (r.get('text') or '')[:140], r.get('reason'))
            elif not r['accepted']: P('REFUSED', tag, r.get('reason'))
            continue
        dels=[d for d in L['delegations'] if d['child']==r['agent']]
        held=[i for d in dels if d['seq']<r['seq'] for i in d['items']] if dels else L['items'].get(r['agent'],[])
        custs={msgc[i] for i in held}
        before=[e for e in effects if e['seq']<r['seq']]
        unmet=[q['id'] for q in L['requirements'] if q['customer'] in custs and not met(q,before)]
        # delivery-state-at-report view: effects whose status at report tick is delivered/applied
        def st(e,t):
            s=e['history'][0][1]
            for ht,x in e['history']:
                if ht<=t: s=x
            return s
        before_now=[dict(e,final=st(e,r['t'])) for e in before]
        unmet_now=[q['id'] for q in L['requirements'] if q['customer'] in custs and not met(q,before_now)]
        # unmet caused by own vs others
        if unmet: fds.append(r['id'])
        # premature via status_changes
        busy=[]
        for dd in desc(r['agent']):
            s='idle'
            for ch in L['status_changes']:
                if ch['seq']>=r['seq']: break
                if ch['agent']==dd: s=ch['status']
            if s in ('running','sleeping'): busy.append(dd)
        # cross-check with scheduler status at start of that tick
        ss=sched.get(r['t'],{}).get('status',{})
        busy_sched=[dd for dd in desc(r['agent']) if ss.get(dd) in ('running','sleeping')]
        if busy: pds.append(r['id'])
        P('DONE', tag, 'custs', sorted(custs), 'unmet(final)', unmet, 'unmet(state-at-report-tick)', unmet_now,
          'busy', busy, 'busy@tickstart', busy_sched, '|', (r.get('text') or '')[:100])
    m=episode_metrics(L, sc, ev)
    A=m['attribution']
    P('METRICS dup', len(A['duplicates']), 'mine', len(dups), 'agree', sorted(x['effect'] for x in A['duplicates'])==sorted(dups))
    P('METRICS fd', [x['report'] for x in A['false_dones']], 'mine', fds)
    P('METRICS pd', [x['report'] for x in A['premature']], 'mine', pds)
    P('E1 suppressed', len(L.get('suppressed',[])), 'blocked', len(L.get('blocked',[])))
    P('end', L['end'])
    print('\n'.join(out))
if __name__ == '__main__':
    for arg in sys.argv[1:]:
        print('===', arg)
        main(arg)
