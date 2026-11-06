**This section is a demo of the build pipeline on scripted (non-LLM) teams. It is not a result of the study.** In the demo, "A" is the scripted naive team and "B" the scripted careful team ({{S:episodes_included}} episodes, fault profile {{T1:A:B0:faults}}).

The naive team in B0 had a DAR of {{T1:A:B0:dar_full}} per 100 required effects (seed-bootstrap 95% interval), and {{T1:A:B0:dup_episodes_kn}} of its episodes had a duplicate. Under E1 its DAR was {{T1:A:E1:dar}}. Its lead's done reports were false in {{T1:A:B0:fcr_lead_ci}} of B0 episodes and in {{T1:A:E2:fcr_lead_ci}} under the read-back gate (E2), where the gate cost {{T1:A:E2:steps_overhead_vs_b0|signpct}} scheduler turns. For B0 against E1 on episodes with a duplicate, the Holm-adjusted Fisher p-value was {{TT:A:E1:any_duplicate:p_holm|p}}. Over all naive episodes the team made {{EP:A:*:*:effects|sum;,.0f}} side effects.

!csv[Scripted demo, shapes pooled: DAR per 100 required effects with seed-bootstrap 95% interval, false completion over all accepted done reports, and task success, with 95% Wilson intervals.](results/{grid}/table1.csv){#tab:demo cols=model:team,condition:cond,n_episodes:n,dar_full:DAR/100,fcr_all_ci:FCR,pcr_all_kn:PCR,task_success_ci:task_success where=shape=all}

![Scripted demo. (a) Failures by condition; (b) measured DAR of the scripted teams against the closed-form expectation. Both produced by `scripts/make_figures.py` from the demo tables.](figures/{grid}/fig1_failures_by_condition.png, figures/{grid}/fig_validation_scripted.png){#fig:demo1}

@tab:demo and @fig:demo1 show that tables, figures, captions and cross-references are produced by the pipeline.
