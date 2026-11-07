/* Framework-free, no CDN. API key is in RAM only. Never interpolate untrusted HTML. */
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (date) => date ? new Date(date).toLocaleString() : '—';
  const short = (text, n=68) => String(text || '').slice(0,n) + (String(text || '').length>n?'…':'');
  const titleCase = (s) => String(s||'').replace(/_/g,' ');
  const state = {apiKey:'',tasks:[],metrics:{},memory:[],audit:[],knowledge:[],evaluations:[],evaluationSummary:{},benchmarks:[],benchmarkManifest:{},tab:'overview',timer:null,benchmarkSubmitting:false,benchmarkCompare:null,benchmarkCompareKey:'',v7Manifest:{},v7Studies:[],v7SelectedId:'',v7Detail:null,v7Submitting:false,h8Manifest:null,h8Cases:[],h8Summary:null,h8Selected:'',h8Current:null};
  const toast = (text, error=false) => {const el=$('#toast');el.textContent=text;el.className='toast visible'+(error?' error':'');clearTimeout(window.toastTimeout);window.toastTimeout=setTimeout(()=>el.classList.remove('visible'),3700)};
  async function request(path, opts={}) {
    const response=await fetch(path,{...opts,headers:{'X-API-Key':state.apiKey,...(opts.headers||{})}});
    if (!response.ok) {let msg=`HTTP ${response.status}`;try{msg=(await response.json()).detail||msg}catch{}throw new Error(typeof msg==='string'?msg:JSON.stringify(msg))}
    if(response.status===204)return null;
    return response.json();
  }
  async function refresh() {
    if(!state.apiKey)return;
    try{
      const [metrics,tasks,memory,audit,knowledge,evaluations,evaluationSummary,benchmarks,benchmarkManifest,v7Manifest,v7Studies]=await Promise.all([request('/api/metrics'),request('/api/tasks?limit=100'),request('/api/memory?limit=50'),request('/api/audit?limit=70'),request('/api/knowledge/documents?limit=100'),request('/api/evaluations?limit=100'),request('/api/evaluations/summary'),request('/api/benchmarks/runs?limit=20&dataset_id=agentops-synthetic-gold-v1'),request('/api/benchmarks/manifest'),request('/api/reliability/manifest'),request('/api/reliability/studies?limit=12')]);
      Object.assign(state,{metrics,tasks,memory,audit,knowledge,evaluations,evaluationSummary,benchmarks,benchmarkManifest,v7Manifest,v7Studies});render();loadBenchmarkCompare();if(state.tab==='reliability')loadReliabilityDetail();if(state.tab==='single_review')s81SelectStudies();
      $('#healthDot').classList.remove('offline');$('#healthText').textContent='Control plane connected';$('#lastSync').textContent='Synced '+new Date().toLocaleTimeString();
    }catch(e){$('#healthDot').classList.add('offline');$('#healthText').textContent='Connection issue';toast(e.message,true)}
  }
  function tab(name){state.tab=name;$$('.nav-link').forEach(el=>el.classList.toggle('active',el.dataset.tab===name));$$('.tab-page').forEach(el=>el.classList.toggle('active',el.id===name));const link=$$('.nav-link').find(el=>el.dataset.tab===name);const display=link?.querySelector('.nav-text')?.textContent.trim()||name;$('#breadcrumb').textContent=display;document.title=display+' · NastaraOps';render();if(name==='reliability')loadReliabilityDetail();if(name==='human_review')loadHumanReview();if(name==='single_review')loadSingleReview();if(name==='research_v9')loadResearchV9()}
  async function loadResearchV9(){
    if(!state.apiKey)return;
    const out=$('#v9Overview');if(!out)return;
    out.innerHTML='<div class="empty">Loading versioned public research snapshot…</div>';
    try{
      const result=await request('/api/research/v9/summary');
      const study=result.study||{},human=result.human_review||{};
      const pct=v=>v==null?'N/A':(100*Number(v)).toFixed(1)+'%';
      $('#v9Acc').textContent=pct(study.mean_accuracy);
      $('#v9F1').textContent=pct(study.mean_macro_f1);
      $('#v9Agreement').textContent=pct(study.agreement);
      $('#v9Wrong').textContent=String(study.wrong_both??'—');
      const ci=(x)=>Array.isArray(x)&&x.length===2?pct(x[0])+' — '+pct(x[1]):'N/A';
      out.innerHTML=`<div class="h8-case-body"><p><strong>Qwen:</strong> ${esc(result.judge_model_tag||'—')} · frozen Judge ${esc(result.runner_version||'—')}</p><p><strong>Accuracy 95% descriptive interval:</strong> ${esc(ci(study.ci_accuracy_95))}</p><p><strong>Macro-F1 95% descriptive interval:</strong> ${esc(ci(study.ci_macro_f1_95))}</p><p><strong>Human first votes:</strong> ${esc(human.first_votes??'N/A')} · spreadsheet imports ${esc(human.spreadsheet_imported_nonblind_assurance??'N/A')} · AI-suggestion agreement ${esc(pct(human.ai_suggestion_agreement_descriptive))} (not an independent score)</p><p><strong>Disagreement to check:</strong> ${esc((human.curated_gold_disagreements||[]).join(', ')||'none')} · historical first votes are never rewritten</p><p><strong>Frozen dataset SHA256:</strong> <code>${esc(result.dataset_sha256||'')}</code></p><p><strong>Frozen Judge SHA256:</strong> <code>${esc(result.judge_source_sha256||'')}</code></p></div>`;
      $('#v9Message').textContent='Published snapshot verified · Source study: 2 completed repetitions · Separate from live PostgreSQL review state.';
    }catch(err){out.innerHTML='<div class="empty knowledge-error">'+esc(err.message)+'</div>';$('#v9Message').textContent='Research report is not available: '+err.message;}
  }
  async function v9Download(kind){
    if(!state.apiKey)return toast('Connect API key first',true);
    try{
      const link=kind==='md'?'/api/research/v9/report.md':'/api/research/v9/summary';
      const response=await fetch(link,{headers:{'X-API-Key':state.apiKey}});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download=kind==='md'?'NastaraOps_Research_Report.md':'NastaraOps_Research_Metrics.json';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(err){toast('Research report download failed: '+err.message,true)}
  }
  function pill(status){return `<span class="status-pill ${esc(status)}">${esc(titleCase(status))}</span>`}
  function taskTable(tasks){if(!tasks.length)return '<div class="empty">No workflows found. Create one to see your agents in action.</div>';
    return `<div class="table-scroll"><table><thead><tr><th>WORKFLOW</th><th>AGENT</th><th>STATUS</th><th>CREATED</th><th></th></tr></thead><tbody>${tasks.map(t=>`<tr><td><strong>${esc(short(t.title,40))}</strong><div class="subline">${esc(t.id.slice(0,8))}</div></td><td class="kind-label">${esc(titleCase(t.kind))}</td><td>${pill(t.status)}</td><td>${esc(fmt(t.created_at))}</td><td><button class="table-action" data-inspect="${esc(t.id)}">Inspect →</button></td></tr>`).join('')}</tbody></table></div>`}
  function render(){
    if(!state.apiKey)return;
    const m=state.metrics;$('#sTotal').textContent=m.total??0;$('#sCompleted').textContent=m.completed??0;$('#sPending').textContent=m.awaiting_approval??0;$('#sRate').textContent=(m.completion_rate_pct??0)+'%';$('#sLatency').textContent=m.avg_duration_ms>=1000?(m.avg_duration_ms/1000).toFixed(1)+'s':(m.avg_duration_ms??0)+'ms';$('#navPending').textContent=m.awaiting_approval??0;$('#memoryCount').textContent=(m.memory_count||0)+' records';
    const kinds=['email','document','request','report'],max=Math.max(1,...kinds.map(k=>m.by_kind?.[k]||0));
    $('#distribution').innerHTML=kinds.map(k=>`<div class="bar-line"><div class="bar-label"><span>${esc(k)} agent</span><b>${m.by_kind?.[k]||0}</b></div><div class="bar-track"><div class="bar-fill" style="width:${100*(m.by_kind?.[k]||0)/max}%"></div></div></div>`).join('');
    $('#recentTasks').innerHTML=taskTable(state.tasks.slice(0,6));
    renderTasks();
    const pending=state.tasks.filter(t=>t.status==='awaiting_approval');
    $('#approvalList').innerHTML=pending.length?pending.map(t=>`<article class="approval-card"><div class="approval-head"><div><h3>${esc(t.title)}</h3><small>${esc(titleCase(t.kind))} agent · ${esc(fmt(t.created_at))}</small></div>${pill(t.status)}</div><p class="approval-desc">${esc(short(t.draft_json?.summary||t.content,360))}</p><div class="risk-tags">${(t.risk_reasons||[]).map(r=>`<span class="risk-tag">${esc(titleCase(r))}</span>`).join('')}</div><div class="approval-actions"><button class="btn btn-outline" data-inspect="${esc(t.id)}">Inspect draft</button><button class="btn btn-danger" data-decision="${esc(t.id)}" data-approved="false">Reject</button><button class="btn btn-primary" data-decision="${esc(t.id)}" data-approved="true">Approve</button></div></article>`).join(''):'<div class="panel empty">All clear. No workflows are awaiting human review.</div>';
    $('#memoryList').innerHTML=state.memory.length?state.memory.map(e=>`<div class="memory-item"><div class="memory-icon">▤</div><div><h3>${esc(titleCase(e.kind))} · ${esc(e.task_id.slice(0,8))} <span class="subline">${esc(fmt(e.created_at))}</span></h3><p>${esc(e.summary)}</p></div></div>`).join(''):'<div class="empty">Approved workflow summaries will appear here.</div>';
    $('#auditList').innerHTML=state.audit.length?state.audit.map(e=>`<div class="audit-item"><div class="audit-icon">⟡</div><div><h3>${esc(titleCase(e.action))} <small>by ${esc(e.actor)} · ${esc(fmt(e.created_at))}</small></h3><p>Task ${esc(e.task_id?.slice(0,8)||'system')} · ${esc(JSON.stringify(e.detail_json))}</p><p class="subline">SHA256 ${esc(e.event_hash.slice(0,20))}…</p></div></div>`).join(''):'<div class="empty">No audit events recorded yet.</div>';
    renderKnowledge();
    renderEvaluations();
    renderBenchmarks();
    renderReliability();
  }
  function renderTasks(){const term=$('#taskSearch').value.toLowerCase(),status=$('#statusFilter').value;$('#allTasks').innerHTML=taskTable(state.tasks.filter(t=>(!status||t.status===status)&&[t.title,t.kind,t.id].some(s=>s.toLowerCase().includes(term))))}
  function renderKnowledge(){
    $('#knowledgeCount').textContent=`${state.knowledge.length} SOURCES`;
    $('#knowledgeDocuments').innerHTML=state.knowledge.length?state.knowledge.map(d=>`<article class="knowledge-doc"><div class="knowledge-doc-icon">▤</div><div class="knowledge-doc-main"><h3>${esc(d.title)}</h3><p>${esc(d.filename)} · ${d.chunk_count} chunks · ${esc(fmt(d.created_at))}</p>${d.error?`<p class="knowledge-error">${esc(d.error)}</p>`:''}</div><div class="knowledge-doc-actions"><span class="status-pill ${d.status==='ready'?'completed':d.status==='failed'?'failed':'running'}">${esc(d.status)}</span>${(d.status==='ready'||d.status==='failed')?`<button class="table-action" data-knowledge-delete="${esc(d.id)}" data-knowledge-name="${esc(d.title)}">Delete</button>`:''}</div></article>`).join(''):'<div class="empty">No knowledge documents indexed yet. Upload a trusted source to begin.</div>';
  }
  function renderEvaluations(){
    const summary=state.evaluationSummary||{}, cov=summary.metric_coverage||{};
    const pct=(k)=>cov[k]?.mean==null?'N/A':(100*cov[k].mean).toFixed(0)+'%';
    $('#evTaskCount').textContent=summary.tasks_evaluated??0;
    $('#evQuestionRate').textContent=pct('question_tracking_rate');
    $('#evCitationRate').textContent=pct('citation_id_integrity');
    $('#evJudgeCount').textContent=summary.judge_runs??0;
    $('#evQuestionCoverage').textContent=`${cov.question_tracking_rate?.evaluated||0} scored tasks`;
    $('#evCitationCoverage').textContent=`${cov.citation_id_integrity?.evaluated||0} scored tasks · IDs only`;
    const select=$('#evaluationTask'),previous=select.value;
    const available=state.tasks.filter(t=>(t.status==='completed'||t.status==='rejected')&&t.draft_json);
    select.innerHTML=available.length?available.map(t=>`<option value="${esc(t.id)}">${esc(short(t.title,52))} · ${esc(t.id.slice(0,8))}</option>`).join(''):'<option value="">No finalized workflow</option>';
    if(available.some(t=>t.id===previous))select.value=previous;
    $('#evaluationCount').textContent=`${state.evaluations.length} RUNS`;
    $('#evaluationList').innerHTML=state.evaluations.length?`<div class="table-scroll"><table><thead><tr><th>WORKFLOW</th><th>MODE</th><th>STATUS</th><th>QUESTION TRACKING</th><th>CITATION IDS</th><th>RUN DATE</th><th></th></tr></thead><tbody>${state.evaluations.map(e=>{const t=state.tasks.find(t=>t.id===e.task_id);const m=e.metrics_json||{};const val=(key)=>m[key]==null?'N/A':(100*Number(m[key])).toFixed(0)+'%';return `<tr><td><strong>${esc(short(t?.title||e.task_id,35))}</strong><div class="subline">${esc(e.task_id.slice(0,8))}</div></td><td>${esc(titleCase(e.mode))}</td><td>${pill(e.status)}</td><td>${val('question_tracking_rate')}</td><td>${val('citation_id_integrity')}</td><td>${esc(fmt(e.created_at))}</td><td><button class="table-action" data-eval-inspect="${esc(e.id)}">Inspect →</button></td></tr>`}).join('')}</tbody></table></div>`:'<div class="empty">No evaluations yet. Complete a workflow or run a historical check.</div>';
  }
  function inspectEvaluation(id){
    const run=state.evaluations.find(e=>e.id===id);if(!run)return;
    const t=state.tasks.find(t=>t.id===run.task_id);
    modal(t?.title||run.task_id,'QUALITY EVALUATION',`<div class="detail-row"><span>Evaluator</span><b>${esc(run.evaluator_version)} · ${esc(titleCase(run.mode))}</b></div><div class="detail-row"><span>Status</span>${pill(run.status)}</div><div class="detail-row"><span>Judge model</span><b>${esc(run.judge_model||'Not used')}</b></div><div class="detail-row"><span>Created</span><b>${esc(fmt(run.created_at))}</b></div>${run.error?`<p class="knowledge-error">${esc(run.error)}</p>`:''}<h3 class="detail-sub">Scores (null = not evaluated)</h3><pre class="json-view">${esc(JSON.stringify(run.metrics_json||{},null,2))}</pre><h3 class="detail-sub">Evidence &amp; limitations</h3><pre class="json-view">${esc(JSON.stringify(run.details_json||{},null,2))}</pre><div class="modal-footer"><button class="btn btn-outline" id="closeEvaluation">Close</button></div>`);
    $('#closeEvaluation').onclick=closeModal;
  }
  function benchmarkFeedback(message, variant='info'){
    const el=$('#bmQueueFeedback');
    el.textContent=String(message);
    el.className='benchmark-feedback '+variant;
  }
  function renderBenchmarks(){
    const info=state.benchmarkManifest||{},runs=(state.benchmarks||[]).filter(r=>r.dataset_id===info.dataset_id),latest=runs[0],metrics=latest?.metrics_json||{};
    if(!state.benchmarkCompareKey)renderBenchmarkCompare();
    $('#bmTotal').textContent=info.case_count??'—';
    $('#bmCoverage').textContent=metrics.coverage==null?'N/A':(100*metrics.coverage).toFixed(0)+'%';
    $('#bmCoverageHint').textContent=latest?`${metrics.evaluated_count||0} scored / ${latest.total} · ${latest.progress} processed`:'No run yet';
    $('#bmF1').textContent=metrics.macro_f1==null?'N/A':(100*metrics.macro_f1).toFixed(1)+'%';
    $('#bmUnsafe').textContent=metrics.release_policy?.unsafe_allow_count??'N/A';
    const labels=info.labels||{};
    $('#bmDatasetInfo').innerHTML=`<strong>${esc(info.dataset_id||'—')}</strong><p class="form-hint">${esc(info.case_count??0)} cases · ${esc(Object.entries(labels).map(([k,v])=>`${k}: ${v}`).join(' · '))}</p><p class="subline">SHA256: ${esc(short(info.dataset_sha256||'',20))} · ${esc(info.runner_version||'')}</p><p class="form-hint">${esc(info.provenance||'')}</p>`;
    const active=runs.some(r=>['queued','running'].includes(r.status));
    const start=$('#startBenchmark');
    start.disabled=active||state.benchmarkSubmitting;
    start.textContent=state.benchmarkSubmitting?'Submitting request…':active?'Benchmark already queued / running':'▶ Queue Qwen benchmark';
    if(active&&!state.benchmarkSubmitting&&$('#bmQueueFeedback').className==='benchmark-feedback'){
      benchmarkFeedback('An active benchmark already exists. Follow progress in Benchmark history below.', 'info');
    }
    $('#bmRunCount').textContent=`${runs.length} RUNS`;
    $('#benchmarkList').innerHTML=runs.length?`<div class="table-scroll"><table><thead><tr><th>DATASET / MODEL</th><th>STATUS</th><th>PROGRESS</th><th>MACRO F1</th><th>COVERAGE</th><th>DATE</th><th></th></tr></thead><tbody>${runs.map(r=>`<tr><td><strong>${esc(short(r.dataset_id,28))}</strong><div class="subline">${esc(r.judge_model)} · ${esc(r.runner_version)} · ${esc(r.id.slice(0,8))}</div></td><td>${pill(r.status)}</td><td>${r.progress}/${r.total}</td><td>${r.metrics_json?.macro_f1==null?'N/A':(100*r.metrics_json.macro_f1).toFixed(1)+'%'}</td><td>${r.metrics_json?.coverage==null?'N/A':(100*r.metrics_json.coverage).toFixed(0)+'%'}</td><td>${esc(fmt(r.created_at))}</td><td><button class="table-action" data-benchmark-inspect="${esc(r.id)}">Inspect →</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No benchmark runs yet.</div>';
    const matrix=metrics.confusion_matrix;
    $('#bmMatrixTag').textContent=latest?`${esc(latest.status).toUpperCase()} · ${metrics.evaluated_count||0} SCORED`:'NO RUN';
    $('#bmMatrix').innerHTML=matrix&&metrics.evaluated_count?`<div class="table-scroll"><table class="confusion-table"><thead><tr><th>GOLD ↓ / PREDICTION →</th>${Object.keys(matrix).map(k=>`<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${Object.entries(matrix).map(([gold,row])=>`<tr><th>${esc(gold)}</th>${Object.entries(row).map(([pred,v])=>`<td class="${gold===pred?'bm-diagonal':''}">${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="benchmark-summary"><span>Accuracy: <b>${metrics.accuracy_on_evaluated==null?'N/A':(100*metrics.accuracy_on_evaluated).toFixed(1)+'%'}</b></span><span>Macro F1: <b>${metrics.macro_f1==null?'N/A':(100*metrics.macro_f1).toFixed(1)+'%'}</b></span><span>Failed calls: <b>${metrics.error_count||0}</b></span><span>Unsafe predicted releases: <b>${metrics.release_policy?.unsafe_allow_count||0}</b></span></div>`:'<div class="empty">Waiting for successfully judged cases.</div>';
  }
  function renderBenchmarkCompare(){
    const m=state.benchmarkCompare||{},tag=$('#bmCompareTag'),target=$('#bmCompare');
    if(!state.benchmarkCompare){tag.textContent='NOT AVAILABLE';target.innerHTML='<div class="empty">Finish a baseline and calibrated run on the same dataset/model to compare. Previously saved runs are preserved.</div>';return;}
    const pp=v=>v==null?'N/A':(v*100).toFixed(1)+'%';
    const delta=v=>v==null?'N/A':((v>0?'+':'')+(v*100).toFixed(1)+' pp');
    tag.textContent=m.paired_count+' PAIRED CASES';
    const a=m.baseline_on_pairs||{},b=m.calibrated_on_pairs||{};
    target.innerHTML=`<div class="benchmark-summary"><span>Paired: <b>${m.paired_count}/${m.dataset_count}</b></span><span>Baseline accuracy: <b>${pp(a.accuracy)}</b></span><span>Calibrated accuracy: <b>${pp(b.accuracy)}</b></span><span>Accuracy Δ: <b>${delta(m.accuracy_delta)}</b></span><span>Macro-F1 Δ: <b>${delta(m.macro_f1_delta)}</b></span><span>Fixed: <b>${m.improved}</b></span><span>Regressed: <b>${m.regressed}</b></span></div><div class="compare-explainer">${esc((m.warnings||[]).join(' · '))}</div><div class="table-scroll"><table><thead><tr><th>CASE</th><th>GOLD</th><th>BASELINE</th><th>CALIBRATED</th><th>OUTCOME</th></tr></thead><tbody>${(m.label_flips||[]).map(row=>`<tr><td>${esc(row.case_id)}</td><td>${esc(row.gold)}</td><td>${esc(row.baseline)}</td><td>${esc(row.calibrated)}</td><td>${esc(row.outcome)}</td></tr>`).join('')||'<tr><td colspan="5">No predicted labels changed on paired cases.</td></tr>'}</tbody></table></div>`;
  }
  async function loadBenchmarkCompare(){
    const runs=(state.benchmarks||[]).filter(r=>(['completed','completed_errors','completed_with_errors'].includes(r.status)||
      (r.status==='failed'&&r.progress===r.total&&r.metrics_json?.evaluated_count>0)));
    const candidates=runs.filter(r=>['2.2.0-anchor-review','2.1.0-intent-gated','2.0.0-calibrated'].includes(r.runner_version));
    const candidate=candidates[0];
    const baseline=candidate&&runs.find(r=>r.runner_version==='1.0.0'&&r.dataset_sha256===candidate.dataset_sha256&&r.judge_model===candidate.judge_model);
    const key=baseline&&candidate?baseline.id+':'+candidate.id:'';
    if(!key){state.benchmarkCompare=null;state.benchmarkCompareKey='';renderBenchmarkCompare();return;}
    if(key===state.benchmarkCompareKey)return;
    state.benchmarkCompareKey=key;
    try{const result=await request('/api/benchmarks/compare?baseline_id='+encodeURIComponent(baseline.id)+'&candidate_id='+encodeURIComponent(candidate.id));
      if(state.benchmarkCompareKey!==key)return;state.benchmarkCompare=result;renderBenchmarkCompare();
    }catch(err){if(state.benchmarkCompareKey===key){state.benchmarkCompare=null;$('#bmCompare').textContent='Comparison unavailable: '+err.message;$('#bmCompareTag').textContent='ERROR'}}
  }
  async function inspectBenchmark(id){
    try{const run=await request(`/api/benchmarks/runs/${encodeURIComponent(id)}`);const m=run.metrics_json||{};
      modal('Gold benchmark · '+run.id.slice(0,8),'SYNTHETIC REFERENCE VALIDATION',`<div class="detail-row"><span>Status</span>${pill(run.status)}</div><div class="detail-row"><span>Model</span><b>${esc(run.judge_model)}</b></div><div class="detail-row"><span>Judge profile</span><b>${esc(run.runner_version)}</b></div><div class="detail-row"><span>Dataset hash</span><code>${esc(run.dataset_sha256.slice(0,20))}…</code></div><div class="detail-row"><span>Progress</span><b>${run.progress} / ${run.total}</b></div>${run.error?`<p class="knowledge-error">${esc(run.error)}</p>`:''}<h3 class="detail-sub">Metrics (only scored cases)</h3><pre class="json-view">${esc(JSON.stringify(m,null,2))}</pre>${m.unscored_case_errors?.length?`<h3 class="detail-sub">Unscored case errors (not counted as predictions)</h3><pre class="json-view">${esc(JSON.stringify(m.unscored_case_errors,null,2))}</pre>`:''}<h3 class="detail-sub">Per-case gold labels and model decisions</h3><pre class="json-view">${esc(JSON.stringify(run.predictions_json||[],null,2))}</pre><div class="modal-footer"><button class="btn btn-outline" id="closeBenchmark">Close</button></div>`);
      $('#closeBenchmark').onclick=closeModal;
    }catch(err){toast(err.message,true)}
  }
  function v7pct(x){return x==null?'N/A':(100*x).toFixed(1)+'%'}
  function renderReliability(){
    const studies=state.v7Studies||[],m=state.v7Manifest||{},latest=studies[0];
    $('#v7Cases').textContent=m.cases||100;
    $('#v7Manifest').innerHTML=`<strong>${esc(m.dataset_id||'Holdout fixture')}</strong><p class="form-hint">${m.cases||100} cases · ${esc(Object.entries(m.labels||{}).map(([k,v])=>k+': '+v).join(' · '))}</p><p class="subline">Data SHA256: ${esc(short(m.dataset_sha256||'',24))}<br>Judge SHA256: ${esc(short(m.judge_source_sha256||'',24))}</p><p class="form-hint">${esc(m.provenance||'')}</p>`;
    $('#v7Progress').textContent=latest?latest.total_processed+'/'+latest.total_calls_planned:'—';
    $('#v7ProgressHint').textContent=latest?latest.status+' · '+latest.total_unscored+' unscored':'No study yet';
    $('#v7F1').textContent=latest?v7pct(latest.mean_macro_f1_on_pairs):'N/A';
    $('#v7Agreement').textContent=latest?v7pct(latest.case_agreement_rate):'N/A';
    $('#v7RunCount').textContent=studies.length+' STUDIES';
    const active=studies.some(r=>['queued','running'].includes(r.status));
    $('#v7Queue').disabled=active||state.v7Submitting;
    $('#v7Queue').textContent=state.v7Submitting?'Submitting…':active?'Study queued or running':'▶ Queue holdout study';
    $('#v7Studies').innerHTML=studies.length?`<div class="table-scroll"><table><thead><tr><th>STUDY</th><th>STATUS</th><th>PROCESSED</th><th>MEAN MACRO-F1</th><th>AGREEMENT</th><th>CREATED</th><th></th></tr></thead><tbody>${studies.map(r=>`<tr><td><strong>${esc(r.study_id.slice(0,8))}</strong><div class="subline">${esc(r.judge_model)} · ${r.repeats_requested} repeats</div></td><td>${pill(r.status)}</td><td>${r.total_processed}/${r.total_calls_planned}</td><td>${v7pct(r.mean_macro_f1_on_pairs)}</td><td>${v7pct(r.case_agreement_rate)}</td><td>${esc(fmt(r.created_at))}</td><td><button class="table-action" type="button" data-v7-inspect="${esc(r.study_id)}">Inspect →</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Queue the first holdout study to see results.</div>';
  }
  async function loadReliabilityDetail(force=false){
    const studies=state.v7Studies||[];
    const target=state.v7SelectedId||studies[0]?.study_id;
    if(!target){$('#v7ReportTag').textContent='NO REPORT';$('#v7Report').innerHTML='<div class="empty">No study selected yet.</div>';$('#v7Exports').hidden=true;return;}
    if(!force&&state.v7Detail?.study_id===target&&['completed','completed_errors','failed'].includes(state.v7Detail.status))return;
    try{
      const r=await request('/api/reliability/studies/'+encodeURIComponent(target));
      if(state.v7SelectedId&&state.v7SelectedId!==target)return;
      state.v7SelectedId=target;state.v7Detail=r;
      $('#v7ReportTag').textContent=r.status.toUpperCase()+' · '+r.paired_count+' COMMON CASES';
      $('#v7Exports').hidden=false;
      const interval=r.confidence_intervals||{};
      const range=x=>x?.length===2?v7pct(x[0])+' — '+v7pct(x[1]):'N/A until every repetition completes successfully';
      $('#v7Report').innerHTML=`<div class="v7-report-grid"><div><small>Mean accuracy</small><strong>${v7pct(r.mean_accuracy_on_pairs)}</strong></div><div><small>Mean Macro-F1</small><strong>${v7pct(r.mean_macro_f1_on_pairs)}</strong></div><div><small>Accuracy 95% interval</small><strong>${range(interval.accuracy_95)}</strong></div><div><small>Macro-F1 95% interval</small><strong>${range(interval.macro_f1_95)}</strong></div><div><small>Exact repeat agreement</small><strong>${v7pct(r.case_agreement_rate)}</strong></div><div><small>Wrong in all repeats</small><strong>${r.consistently_wrong_cases.length}</strong></div></div><div class="benchmark-summary"><span><b>${r.paired_count}/${r.case_count}</b> jointly scored</span><span><b>${r.total_unscored}</b> unscored calls</span><span><b>${r.case_disagreements.length}</b> unstable cases</span><span>Frozen judge SHA matches: <b>${r.code_hash_matches_current?'YES':'NO'}</b></span></div><div class="compare-explainer" style="padding:14px 24px;color:#a4b2cc;font-size:12px;line-height:1.6">${esc(r.warning)}</div><h3 class="v7-subhead">Per repetition</h3><div class="table-scroll"><table><thead><tr><th>RUN</th><th>STATUS</th><th>PROGRESS</th><th>ACCURACY</th><th>MACRO-F1</th><th>ERRORS</th></tr></thead><tbody>${r.runs.map(x=>`<tr><td>${esc(x.id.slice(0,8))}</td><td>${pill(x.status)}</td><td>${x.progress}/${r.case_count}</td><td>${v7pct(x.accuracy)}</td><td>${v7pct(x.macro_f1)}</td><td>${x.errors}</td></tr>`).join('')}</tbody></table></div><h3 class="v7-subhead">Cases with disagreement (${r.case_disagreements.length})</h3><pre class="json-view v7-json">${esc(JSON.stringify(r.case_disagreements.slice(0,30),null,2))}</pre><h3 class="v7-subhead">Consistently incorrect (${r.consistently_wrong_cases.length})</h3><pre class="json-view v7-json">${esc(JSON.stringify(r.consistently_wrong_cases.slice(0,30),null,2))}</pre>`;
    }catch(err){$('#v7Report').textContent='Reliability report unavailable: '+err.message;$('#v7Exports').hidden=true;}
  }
  async function downloadReliability(kind){
    if(!state.v7SelectedId)return;
    const id=state.v7SelectedId;
    const url='/api/reliability/studies/'+encodeURIComponent(id)+(kind==='json'?'':kind==='predictions'?'/predictions.csv':'/report.'+kind);
    try{
      const response=await fetch(url,{headers:{'X-API-Key':state.apiKey}});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const blob=await response.blob();const temp=URL.createObjectURL(blob);
      const link=document.createElement('a');link.href=temp;link.download='nastaraops-study-'+id.slice(0,8)+'.'+(kind==='predictions'?'predictions.csv':kind);
      document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(temp),1000);
    }catch(err){toast('Export failed: '+err.message,true)}
  }
  function modal(title,eyebrow,body){$('#modalTitle').textContent=title;$('#modalEyebrow').textContent=eyebrow;$('#modalContent').innerHTML=body;$('#modalBackdrop').hidden=false;}
  function closeModal(){$('#modalBackdrop').hidden=true;$('#modalContent').innerHTML=''}
  function newTask(){modal('Create a workflow','AGENT ORCHESTRATION',`<form id="createForm" class="form-fields"><label class="form-field">Workflow type<select name="kind" id="kindSelect" required><option value="email">Email agent — inbox triage and reply draft</option><option value="document">Document agent — structured extraction</option><option value="request">Request agent — service request routing</option><option value="report">Report agent — verified operational metrics</option></select></label><label class="form-field">Title<input name="title" placeholder="e.g. Customer asks for product information" required minlength="3" maxlength="180"></label><label class="form-field">Content<textarea name="content" placeholder="Paste an email, document text, request, or report instructions..." required maxlength="30000"></textarea></label><label class="form-field" id="uploadField" hidden>Or upload a text/PDF document<input type="file" name="file" accept=".pdf,.txt,.md,.docx" /><span class="form-hint">Text-based PDF, Word, Markdown and UTF-8 text files, up to 5 MiB.</span></label><div class="form-row"><label class="form-field">Requested action<select name="action"><option value="">Analysis / draft only</option><option value="send_email">Send email (requires review; no actual send)</option><option value="issue_refund">Issue refund (requires review; no actual transfer)</option><option value="delete_record">Delete record (requires review; no actual delete)</option><option value="export_data">Export data (requires review; no actual export)</option></select></label><label class="form-field">Approval mode<select name="sensitive"><option value="false">Risk-based auto routing</option><option value="true">Always require human approval</option></select></label></div><p class="form-hint">No external write action is executed by this demo. Sensitive cases pause for approval before the analysis is finalized.</p><div class="modal-footer"><button type="button" class="btn btn-outline" id="cancelModal">Cancel</button><button type="submit" class="btn btn-primary">Queue workflow</button></div></form>`);
    $('#cancelModal').onclick=closeModal;
    function syncDocumentInput(){const isDoc=$('#kindSelect').value==='document';$('#uploadField').hidden=!isDoc;const file=$('#uploadField input');const content=$('#createForm textarea[name=content]');content.required=!(isDoc && file.files.length)}
    $('#kindSelect').onchange=syncDocumentInput;$('#uploadField input').onchange=syncDocumentInput;syncDocumentInput();
    $('#createForm').onsubmit=async (e)=>{e.preventDefault();const f=new FormData(e.currentTarget);const metadata={sensitive:f.get('sensitive')==='true'};if(f.get('action'))metadata.requested_action=f.get('action');const body={kind:f.get('kind'),title:f.get('title'),content:f.get('content'),metadata};try{const file=$('#uploadField input').files[0];if(body.kind==='document'&&file){const upload=new FormData();upload.append('title',body.title);upload.append('file',file);upload.append('sensitive',String(metadata.sensitive));if(metadata.requested_action)upload.append('requested_action',metadata.requested_action);await request('/api/tasks/upload',{method:'POST',body:upload});}else{if(!String(body.content).trim())throw new Error('Please provide content or a document file.');await request('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}closeModal();toast('Workflow added to the queue');tab('workflows');await refresh()}catch(err){toast(err.message,true)}};
  }
  function inspect(id){const t=state.tasks.find(x=>x.id===id);if(!t)return;const grounded=t.draft_json?.grounded===true;const sources=t.draft_json?.sources||[];modal(t.title,'WORKFLOW DETAILS',`<div class="detail-row"><span>Task ID</span><b>${esc(t.id)}</b></div><div class="detail-row"><span>Agent</span><b>${esc(titleCase(t.agent||t.kind))}</b></div><div class="detail-row"><span>Status</span>${pill(t.status)}</div><div class="detail-row"><span>Evidence grounding</span><span class="status-pill ${grounded?'completed':'awaiting_approval'}">${grounded?'Cited sources':'Not grounded'}</span></div><div class="detail-row"><span>Risk reasons</span><b>${esc((t.risk_reasons||[]).join(', ')||'None')}</b></div><div class="detail-row"><span>Created</span><b>${esc(fmt(t.created_at))}</b></div><h3 class="detail-sub">Agent execution trace</h3><div class="risk-tags">${(t.trace_json||[]).map(step=>`<span class="risk-tag trace-tag">${esc(titleCase(step))}</span>`).join('')||'<span class="form-hint">Waiting for execution</span>'}</div>${sources.length?`<h3 class="detail-sub">Cited knowledge sources</h3><div class="knowledge-citations">${sources.map(s=>`<div><b>${esc(s.title||s.filename)}</b><small>${esc(s.filename)}${s.page?` · page ${s.page}`:''} · similarity ${Number(s.score||0).toFixed(3)}</small></div>`).join('')}</div>`:''}<h3 class="detail-sub">Original input</h3><pre class="json-view">${esc(t.content)}</pre><h3 class="detail-sub">Agent draft</h3><pre class="json-view">${esc(t.draft_json?JSON.stringify(t.draft_json,null,2):'Not available yet')}</pre>${t.result_json?`<h3 class="detail-sub">Final result</h3><pre class="json-view">${esc(JSON.stringify(t.result_json,null,2))}</pre>`:''}${t.error?`<p class="form-hint">Error: ${esc(t.error)}</p>`:''}<div class="modal-footer"><button class="btn btn-outline" id="closeDetail">Close</button>${t.status==='awaiting_approval'?`<button class="btn btn-primary" data-decision="${esc(t.id)}" data-approved="true">Review and approve</button>`:''}</div>`);$('#closeDetail').onclick=closeModal}
  function decision(id,approved){const t=state.tasks.find(x=>x.id===id);if(!t)return;modal(approved?'Approve recommendation':'Reject recommendation','HUMAN AUTHORIZATION',`<form id="decisionForm" class="form-fields"><p class="form-hint">Your ${approved?'approval':'rejection'} is recorded in the PostgreSQL audit chain and resumes the LangGraph checkpoint. No external side effects are performed.</p><label class="form-field">Reviewer name<input name="reviewer" placeholder="Your name / team" required minlength="2" maxlength="80"></label><label class="form-field">Decision note<textarea name="note" maxlength="1000" placeholder="Reason for your decision (optional)" style="min-height:90px"></textarea></label><div class="modal-footer"><button type="button" class="btn btn-outline" id="cancelDecision">Cancel</button><button type="submit" class="btn ${approved?'btn-primary':'btn-danger'}">Confirm ${approved?'approval':'rejection'}</button></div></form>`);$('#cancelDecision').onclick=closeModal;$('#decisionForm').onsubmit=async(e)=>{e.preventDefault();const f=new FormData(e.currentTarget);try{await request(`/api/tasks/${encodeURIComponent(id)}/decision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved,reviewer:f.get('reviewer'),note:f.get('note')})});closeModal();toast('Decision recorded. Workflow queued for continuation.');await refresh()}catch(err){toast(err.message,true)}}}
  // V8: the API does not disclose curated gold or AI hints before BOTH human votes.
  const h8pct=n=>n==null?'N/A':(100*n).toFixed(1)+'%';
  function h8Identity(){return $('#h8Identity').value.trim()}
  function h8Role(){return $('#h8Role').value}
  function renderHumanQueue(){
    const list=state.h8Cases||[],q=$('#h8Search').value.trim().toLowerCase(),filter=$('#h8Filter').value;
    const shown=list.filter(c=>{
      if(q&&![c.case_id,c.topic,c.category].some(x=>String(x||'').toLowerCase().includes(q)))return false;
      if(filter==='pending')return h8Role()==='reviewer1'?!c.reviewer1_done:h8Role()==='reviewer2'?!c.reviewer2_done:c.status==='conflict';
      return filter==='all'||c.status===filter;
    });
    $('#h8ListCount').textContent=shown.length+' / '+list.length+' CASES';
    $('#h8CaseList').innerHTML=shown.length?shown.map(c=>`<button type="button" class="h8-case-entry ${state.h8Selected===c.case_id?'active':''}" data-h8-case="${esc(c.case_id)}"><span><strong>${esc(c.case_id)}</strong><small>${esc(c.category)} · ${esc(c.topic)}</small></span>${pill(c.status)}</button>`).join(''):'<div class="empty">No cases match the selected filters.</div>';
  }
  function renderHumanStats(){
    const r=state.h8Summary||{},s=r.states||{};
    $('#h8R1').textContent=(r.reviewer1_count||0)+'/100';
    $('#h8R2').textContent=(r.reviewer2_count||0)+'/100';
    $('#h8Final').textContent=(r.finalized_count||0)+'/100';
    $('#h8Conflicts').textContent=s.conflict||0;
    $('#h8Kappa').textContent=`Cohen κ: ${r.cohen_kappa==null?'N/A':Number(r.cohen_kappa).toFixed(3)} · paired ${r.paired_count||0}`;
    $('#h8Summary').innerHTML=`<span>Paired: <b>${r.paired_count||0}/100</b></span><span>Human agreement: <b>${h8pct(r.pair_agreement)}</b></span><span>Human final coverage: <b>${h8pct(r.finalized_coverage)}</b></span><span>AI vs final: <b>${h8pct(r.ai_vs_final_agreement)}</b> (descriptive)</span><span>Conflicts open: <b>${s.conflict||0}</b></span>`;
    const sel=$('#h8Study'),keep=sel.value;
    sel.innerHTML='<option value="">Select a reliability study…</option>'+(state.v7Studies||[]).filter(x=>x.status==='completed').map(x=>`<option value="${esc(x.study_id)}">${esc(x.study_id.slice(0,8))} · ${esc(x.repeats_requested)} repeats</option>`).join('');
    if([...sel.options].some(o=>o.value===keep))sel.value=keep;
  }
  async function loadHumanReview(keepCase=true){
    if(!state.apiKey)return;
    try{
      const [manifest,cases,summary]=await Promise.all([request('/api/human-review/manifest'),request('/api/human-review/cases'),request('/api/human-review/summary')]);
      state.h8Manifest=manifest;state.h8Cases=cases;state.h8Summary=summary;
      renderHumanStats();renderHumanQueue();
      if(keepCase&&state.h8Selected)await openHumanCase(state.h8Selected);
      else if(!state.h8Selected&&cases.length)await openHumanCase(cases[0].case_id);
    }catch(e){$('#h8CaseList').textContent='Cannot load human review: '+e.message;toast(e.message,true)}
  }
  async function openHumanCase(id){
    if(!state.apiKey)return;
    state.h8Selected=id;
    const requestSeq=state.h8RequestSeq=(state.h8RequestSeq||0)+1;
    renderHumanQueue();
    $('#h8CaseTitle').textContent=id;
    $('#h8CaseBody').innerHTML='<div class="empty">Loading evidence…</div>';
    $('#h8DecisionForm').hidden=true;
    try{
      const q=new URLSearchParams({role:h8Role(),reviewer_id:h8Identity()});
      const data=await request('/api/human-review/cases/'+encodeURIComponent(id)+'?'+q.toString());
      if(state.h8Selected!==id||requestSeq!==state.h8RequestSeq)return;
      state.h8Current=data;
      $('#h8CaseTitle').textContent=`${data.case_id} · ${data.category}`;
      $('#h8CaseHint').textContent=`${data.topic} · ${data.status.replaceAll('_',' ')}`;
      $('#h8CaseStatus').textContent=data.ai_revealed?'POST-REVIEW VISIBLE':'BLINDED';
      const r=data.prior_workbook_draft;
      const decision=data.your_decision;
      const sources=(data.sources||[]).map(s=>`<div class="h8-evidence-section"><strong>${esc(s.title||s.source_id)}</strong><div class="h8-metadata">${esc(s.source_id)} · ${esc(s.trust_level)} trust</div><p>${esc(s.text)}</p></div>`).join('');
      const shown=(data.votes||{});
      $('#h8CaseBody').innerHTML=`<div class="h8-evidence-section"><strong>Original question</strong><p>${esc(data.question)}</p></div><div class="h8-evidence-section"><strong>Candidate to judge</strong><p>${esc(data.candidate)}</p></div>${sources}
        ${r?`<div class="h8-evidence-section"><strong>Prior spreadsheet draft (NOT confirmed)</strong><p>${esc(r.label)} — ${esc(r.reason)}</p></div>`:''}
        ${decision?`<div class="h8-evidence-section"><strong>Your previously submitted vote</strong><p>${esc(decision.label)} · ${esc(decision.reason)}</p></div>`:''}
        ${data.ai_revealed?`<div class="h8-evidence-section h8-ai"><strong>AI suggestion — NOT a human review</strong><p>${esc(data.ai_suggestion.label)}: ${esc(data.ai_suggestion.reason)}</p></div><div class="h8-evidence-section"><strong>Recorded human decisions</strong><p>${Object.entries(shown).map(([k,v])=>esc(k)+' · '+esc(v.label)+' ('+esc(v.reviewer_id)+')').join(' | ')}</p>${data.final_label?`<p style="margin-top:9px">Final: ${esc(data.final_label)}</p>`:''}</div>`:''}`;
      if(!h8Identity()){$('#h8CaseBody').innerHTML+='<div class="benchmark-feedback error">Enter a reviewer ID above to enable saving a decision.</div>';return;}
      const adjud=h8Role()==='adjudicator';
      const canAdjud=adjud&&data.status==='conflict';
      const locked=data.status==='adjudicated';
      $('#h8DecisionForm').hidden=!(canAdjud||!adjud&&!locked);
      $('#h8Save').textContent=adjud?'Save third-person adjudication':'Save human vote';
      $('#h8Feedback').textContent=locked?'This case was adjudicated and is locked.':'Votes are append-only; previous revisions are preserved.';
      $('#h8Feedback').className='benchmark-feedback';
      $('#h8Label').value=decision?.label||r?.label||'';
      $('#h8Reason').value=decision?.reason||r?.reason||'';
      $('#h8Confidence').value=String(decision?.confidence||r?.confidence||5);
    }catch(err){$('#h8CaseBody').textContent='Cannot load case: '+err.message;toast(err.message,true)}
  }
  async function saveHumanDecision(e){
    e.preventDefault();if(!state.h8Selected)return;
    const role=h8Role(),reviewer_id=h8Identity();
    if(reviewer_id.length<2){toast('Enter a reviewer ID with at least 2 characters',true);return}
    const payload={case_id:state.h8Selected,reviewer_id,label:$('#h8Label').value,reason:$('#h8Reason').value.trim(),confidence:Number($('#h8Confidence').value)};
    if(payload.reason.length<12){toast('An evidence-based reason (12+ characters) is required',true);return;}
    if(!payload.label){toast('Choose an evidence verdict',true);return;}
    const button=$('#h8Save'),feedback=$('#h8Feedback');
    button.disabled=true;feedback.textContent='Saving an append-only reviewer event…';feedback.className='benchmark-feedback loading';
    try{
      const endpoint=role==='adjudicator'?'/api/human-review/adjudicate':'/api/human-review/decisions';
      if(role!=='adjudicator')payload.role=role;
      const r=await request(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      feedback.textContent='Saved: '+r.decision_id;feedback.className='benchmark-feedback success';
      await loadHumanReview(true);toast('Human review recorded in audit log');
    }catch(err){feedback.textContent='Not saved: '+err.message;feedback.className='benchmark-feedback error';}
    finally{button.disabled=false;}
  }
  async function downloadHumanCsv(){
    try{
      const res=await fetch('/api/human-review/export.csv',{headers:{'X-API-Key':state.apiKey}});
      if(!res.ok)throw new Error('HTTP '+res.status);
      const blob=await res.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download='nastaraops-human-review.csv';a.click();URL.revokeObjectURL(url);
    }catch(err){toast('Export failed: '+err.message,true)}
  }
  async function compareHumanStudy(){
    const id=$('#h8Study').value;if(!id)return toast('Choose a completed reliability study',true);
    $('#h8Comparison').textContent='Computing matched human-vs-model metrics…';
    try{
      const r=await request('/api/human-review/compare/'+encodeURIComponent(id));
      const pp=n=>n==null?'N/A':(100*n).toFixed(1)+'%';
      $('#h8Comparison').innerHTML=`<div class="benchmark-summary"><span>Finalized human cases: <b>${r.finalized_count}/100</b></span>${r.results.map((x,i)=>`<span>Run ${i+1} · matched <b>${x.matched_scored}/${x.finalized_total}</b> · accuracy <b>${pp(x.accuracy)}</b> · Macro-F1 <b>${pp(x.macro_f1)}</b></span>`).join('')}</div><p class="form-hint">${esc(r.warning)}</p>${r.results.map((x,i)=>`<h3 class="v7-subhead">Run ${i+1} · model errors by class and language</h3><div class="table-scroll"><table class="confusion-table"><thead><tr><th>HUMAN ↓ / MODEL →</th>${Object.keys(x.matrix||{}).map(label=>`<th>${esc(label)}</th>`).join('')}</tr></thead><tbody>${Object.entries(x.matrix||{}).map(([gold,entries])=>`<tr><th>${esc(gold)}</th>${Object.entries(entries).map(([label,count])=>`<td class="${gold===label?'bm-diagonal':''}">${count}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="benchmark-summary">${Object.entries(x.languages||{}).map(([language,v])=>`<span>${esc(language.toUpperCase())}: <b>${v.correct}/${v.matched}</b> correct</span>`).join('')}</div><div class="compare-explainer">Error taxonomy: ${esc(JSON.stringify(x.error_taxonomy||{}))}</div>`).join('')}`;
    }catch(err){$('#h8Comparison').textContent='Comparison unavailable: '+err.message}
  }

  // V8.1 single reviewer. Back-end only reveals AI suggestions AFTER an existing Reviewer 1 vote.
  const s81pct = x => x==null ? 'N/A' : (100*Number(x)).toFixed(1)+'%';
  const s81Id = () => $('#s81Identity').value.trim();
  function s81SelectStudies(){
    const select=$('#s81Study'), old=select.value;
    select.innerHTML='<option value="">Choose a completed reliability study</option>'+(state.v7Studies||[])
      .filter(x=>x.status==='completed')
      .map(x=>`<option value="${esc(x.study_id)}">${esc(x.study_id.slice(0,8))} · ${Number(x.repeats_requested)} runs</option>`).join('');
    if(old && [...select.options].some(x=>x.value===old)) select.value=old;
    else if(select.options.length>1) select.selectedIndex=1;
  }
  function s81RenderQueue(){
    const items=state.s81Cases||[], q=$('#s81Search').value.trim().toLowerCase(), filter=$('#s81Filter').value;
    const visible=items.filter(c=>{
      if(q&&![c.case_id,c.category,c.topic].some(v=>String(v||'').toLowerCase().includes(q)))return false;
      if(filter==='pending')return !c.reviewed&&!c.locked_other_reviewer;
      if(filter==='reviewed')return c.reviewed;
      if(filter==='locked')return c.locked_other_reviewer;
      return true;
    });
    $('#s81ListCount').textContent=`${visible.length} / ${items.length} CASES`;
    $('#s81Queue').innerHTML=visible.length?visible.map(c=>`<button type="button" class="h8-case-entry ${state.s81Selected===c.case_id?'active':''}" data-s81-case="${esc(c.case_id)}"><span><strong>${esc(c.case_id)}</strong><small>${esc(c.category)} · ${esc(c.topic)}</small></span><span class="status-pill ${c.reviewed?'completed':c.locked_other_reviewer?'failed':'running'}">${c.reviewed?'Reviewed':c.locked_other_reviewer?'Other ID':'Pending'}</span></button>`).join(''):'<div class="empty">No matching cases. Switch to All cases if you need to revisit a completed vote.</div>';
  }
  async function loadSingleReview(keep=true){
    if(!state.apiKey)return;
    if(s81Id().length<2){$('#s81Queue').textContent='Enter the Reviewer 1 ID used for your earlier votes.';return}
    try{
      const q='?reviewer_id='+encodeURIComponent(s81Id());
      const [cases,stats]=await Promise.all([request('/api/human-review/single/cases'+q),request('/api/human-review/single/summary'+q)]);
      state.s81Cases=cases;state.s81Stats=stats;
      if(!(state.v7Studies||[]).length){try{state.v7Studies=await request('/api/reliability/studies?limit=12')}catch{}}
      $('#s81Done').textContent=`${stats.reviewed_count}/100`;
      $('#s81Pending').textContent=String(stats.unreviewed_count);
      $('#s81Agreement').textContent=s81pct(stats.initial_blind_ai_agreement);
      $('#s81Revisions').textContent=String(stats.total_revisions);
      $('#s81ReportStatus').innerHTML=`<span>Human first votes: <b>${stats.reviewed_count}/100</b></span><span>Imported from spreadsheets: <b>${stats.spreadsheet_imported_first_votes||0}</b></span><span>Single-reviewer coverage: <b>${s81pct(stats.review_coverage)}</b></span><span>Two-human agreement / κ: <b>N/A</b></span>`;
      s81SelectStudies();s81RenderQueue();
      if(keep&&state.s81Selected)await s81Open(state.s81Selected);
      else {
        const next=cases.find(c=>!c.reviewed&&!c.locked_other_reviewer)||cases[0];
        if(next)await s81Open(next.case_id);
      }
    }catch(e){$('#s81Queue').textContent='Cannot load single-reviewer data: '+e.message;toast(e.message,true)}
  }
  async function s81Open(caseId){
    if(!state.apiKey||s81Id().length<2)return;
    state.s81Selected=caseId;
    state.s81Req=(state.s81Req||0)+1;
    const num=state.s81Req;
    s81RenderQueue();
    $('#s81CaseTitle').textContent=caseId;
    $('#s81CaseHint').textContent='Loading…';
    $('#s81Evidence').textContent='Loading evidence…';
    $('#s81Form').hidden=true;
    try{
      const d=await request(`/api/human-review/single/cases/${encodeURIComponent(caseId)}?reviewer_id=${encodeURIComponent(s81Id())}`);
      if(state.s81Selected!==caseId||state.s81Req!==num)return;
      state.s81Current=d;
      $('#s81CaseTitle').textContent=`${d.case_id} · ${d.category}`;
      $('#s81CaseHint').textContent=d.topic;
      $('#s81Blind').textContent=d.blind?'BLINDED':'POST-VOTE · AI VISIBLE';
      const sources=(d.sources||[]).map(x=>`<div class="h8-evidence-section"><strong>${esc(x.title||x.source_id)}</strong><p class="h8-metadata">${esc(x.source_id)} · ${esc(x.trust_level)} trust</p><p>${esc(x.text)}</p></div>`).join('');
      $('#s81Evidence').innerHTML=`<div class="h8-evidence-section"><strong>Question</strong><p>${esc(d.question)}</p></div><div class="h8-evidence-section"><strong>Claim / candidate</strong><p>${esc(d.candidate)}</p></div>${sources}`+
        (d.your_initial_blind_vote?`<div class="h8-evidence-section"><strong>Your INITIAL recorded vote (preserved)</strong><p>${esc(d.your_initial_blind_vote.label)} · ${esc(d.your_initial_blind_vote.reason)}</p></div>`:'')+
        (d.ai_suggestion?`<div class="h8-evidence-section h8-ai"><strong>Assistant AI suggestion — not a human vote</strong><p>${esc(d.ai_suggestion.label)} · ${esc(d.ai_suggestion.reason)}</p></div>`:'');
      if(d.locked_other_reviewer){$('#s81Evidence').innerHTML+='<div class="benchmark-feedback error">Reviewer 1 for this case was claimed by a different ID. Use your original Reviewer ID; do not impersonate a second reviewer.</div>';return}
      $('#s81Form').hidden=false;
      $('#s81Label').value=d.your_latest_vote?.label||'';
      $('#s81Reason').value=d.your_latest_vote?.reason||'';
      $('#s81Confidence').value=String(d.your_latest_vote?.confidence||5);
      $('#s81Save').textContent=d.has_vote?'Save correction (not blind)':'Save FIRST blind vote';
      $('#s81Feedback').textContent=d.has_vote?'Corrections remain in audit history; primary Qwen score uses your first blind vote.':'No AI or model prediction is visible before this first vote.';
      $('#s81Feedback').className='benchmark-feedback';
    }catch(e){$('#s81Evidence').textContent='Cannot load case: '+e.message;}
  }
  async function s81SaveVote(event){
    event.preventDefault();
    if(!state.s81Selected)return;
    const payload={case_id:state.s81Selected,role:'reviewer1',reviewer_id:s81Id(),label:$('#s81Label').value,reason:$('#s81Reason').value.trim(),confidence:Number($('#s81Confidence').value)};
    if(payload.reviewer_id.length<2||!payload.label||payload.reason.length<12){$('#s81Feedback').textContent='Enter a reviewer ID, verdict, and a reason of at least 12 characters.';$('#s81Feedback').className='benchmark-feedback error';return}
    const button=$('#s81Save');button.disabled=true;
    $('#s81Feedback').textContent='Saving append-only human vote…';$('#s81Feedback').className='benchmark-feedback loading';
    try{
      await request('/api/human-review/decisions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      await loadSingleReview(true);
      $('#s81Feedback').textContent='Vote saved in PostgreSQL. First blind vote is retained; model suggestion now available for this case.';
      $('#s81Feedback').className='benchmark-feedback success';
    }catch(e){$('#s81Feedback').textContent='Could not save: '+e.message;$('#s81Feedback').className='benchmark-feedback error';}
    finally{button.disabled=false}
  }
  function s81NextUnreviewed(){
    const cases=state.s81Cases||[];
    const start=cases.findIndex(c=>c.case_id===state.s81Selected);
    const next=[...cases.slice(start+1),...cases.slice(0,start+1)].find(c=>!c.reviewed&&!c.locked_other_reviewer);
    if(next){$('#s81Filter').value='pending';s81Open(next.case_id)}
    else $('#s81Feedback').textContent='No remaining cases for this Reviewer ID. All eligible cases are reviewed.';
  }
  async function s81Export(kind){
    if(s81Id().length<2)return toast('Enter your previous Reviewer ID',true);
    try{
      const response=await fetch('/api/human-review/single/export.'+kind+'?reviewer_id='+encodeURIComponent(s81Id()),{headers:{'X-API-Key':state.apiKey}});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const blob=await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
      link.href=url;link.download='nastaraops-single-review.'+kind;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){toast('Export failed: '+e.message,true)}
  }
  async function s81Compare(){
    const id=$('#s81Study').value;
    if(!id)return toast('Choose a completed reliability study',true);
    $('#s81Report').textContent='Computing matched-case metrics from first blind human votes…';
    try{
      const report=await request('/api/human-review/single/compare/'+encodeURIComponent(id)+'?reviewer_id='+encodeURIComponent(s81Id()));
      const labels=['supported','unsupported','contradicted','abstention'];
      $('#s81Report').innerHTML=`<p class="form-hint">${esc(report.warning)} · Spreadsheet-imported first votes: ${Number(report.spreadsheet_imported_first_votes||0)}</p>`+report.results.map((r,i)=>`<h3 class="v7-subhead">Qwen run ${i+1} · ${esc(r.run_id.slice(0,8))}</h3>
        <div class="benchmark-summary"><span>Matched: <b>${r.matched_scored}/${r.human_reviewed}</b> (of 100)</span><span>Accuracy: <b>${s81pct(r.accuracy)}</b></span><span>Observed-class Macro-F1: <b>${s81pct(r.observed_class_macro_f1)}</b></span><span>Human classes present: <b>${r.observed_classes}/4</b></span></div>
        <div class="table-scroll"><table class="confusion-table"><thead><tr><th>HUMAN ↓ / QWEN →</th>${labels.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${labels.map(x=>`<tr><th>${esc(x)}</th>${labels.map(y=>`<td class="${x===y?'bm-diagonal':''}">${Number(r.matrix[x][y])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
        <div class="table-scroll"><table><thead><tr><th>Class</th><th>Support</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>${labels.map(x=>{const v=r.class_metrics[x];return `<tr><td>${esc(x)}</td><td>${v.support}</td><td>${s81pct(v.precision)}</td><td>${s81pct(v.recall)}</td><td>${s81pct(v.f1)}</td></tr>`}).join('')}</tbody></table></div>
        <div class="compare-explainer">Disagreements on reviewed cases (${r.disagreements.length}): ${r.disagreements.map(x=>esc(x.case_id)+' · '+esc(x.human_initial)+' → '+esc(x.model_verdict)).join(' | ')||'None'}</div>
        <p class="form-hint">${esc(r.warning)}</p>`).join('');
    }catch(e){$('#s81Report').textContent='Comparison failed: '+e.message}
  }

  $$('.nav-link').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
  document.querySelector('[data-brand-home]')?.addEventListener('click',e=>{e.preventDefault();tab('overview')});
  $('#connectForm').addEventListener('submit',async(e)=>{e.preventDefault();state.apiKey=$('#apiKey').value;try{await request('/api/metrics');$('#connectPanel').hidden=true;$('#workspace').hidden=false;$('#apiKey').value='';const health=await fetch('/health').then(r=>r.json());$('#modeChip').textContent='MODE: '+String(health.llm_mode).toUpperCase();await refresh();state.timer=setInterval(refresh,5000)}catch(err){state.apiKey='';toast('Authentication failed: '+err.message,true)}});
  $('#newTaskTop').onclick=()=>state.apiKey?newTask():toast('Connect with an API key first',true);$$('.new-task').forEach(b=>b.onclick=newTask);$('#refreshBtn').onclick=refresh;
  $('#taskSearch').addEventListener('input',renderTasks);$('#statusFilter').addEventListener('change',renderTasks);
  $('#h8DecisionForm').onsubmit=saveHumanDecision;
  $('#h8Save').onclick=saveHumanDecision;
  $('#h8Export').onclick=downloadHumanCsv;
  $('#h8Compare').onclick=compareHumanStudy;
  $('#h8Filter').onchange=renderHumanQueue;
  $('#h8Search').oninput=renderHumanQueue;
  $('#h8Role').onchange=()=>{renderHumanQueue();if(state.h8Selected)openHumanCase(state.h8Selected)};
  $('#h8Identity').onchange=()=>{if(state.h8Selected)openHumanCase(state.h8Selected)};
  // V8.2: file bytes stay in the browser; the server receives them only on the preview/commit request.
  let s82PreviewState = null;
  function s82Reset(){s82PreviewState=null;$('#s82Commit').disabled=true;$('#s82Results').innerHTML=''}
  function s82SetFeedback(message,type='info'){
    $('#s82Feedback').textContent=message;$('#s82Feedback').className='benchmark-feedback '+type;
  }
  function s82Payload(stage){
    const file=$('#s82File').files[0],reviewer=s81Id();
    if(!file)throw new Error('Choose a completed Excel or CSV file.');
    if(reviewer.length<2)throw new Error('Enter your existing reviewer ID first.');
    const fd=new FormData();fd.append('file',file);fd.append('reviewer_id',reviewer);
    if(stage==='commit'){
      fd.append('expected_sha256',s82PreviewState.file_sha256);
      fd.append('confirm','IMPORT_REVIEWER_1_VOTES');
    }
    return fd;
  }
  $('#s82File').onchange=()=>{s82Reset();s82SetFeedback('File selected. Click Preview; no data is saved at this step.');};
  $('#s82Preview').onclick=async()=>{
    s82Reset();$('#s82Preview').disabled=true;s82SetFeedback('Validating Excel and checking existing PostgreSQL votes…','loading');
    try{
      const result=await request('/api/human-review/single/import/preview',{method:'POST',body:s82Payload('preview')});
      s82PreviewState=result;
      const n=result.counts||{}, statuses=[['ready','NEW votes'],['existing','Already saved'],['empty','Empty human columns'],['error','Invalid'],['locked','Other reviewer ID']];
      $('#s82Results').innerHTML=`<div class="s81-import-preview">${statuses.map(([k,label])=>`<span>${esc(label)}: <b>${Number(n[k]||0)}</b></span>`).join('')}</div>`+
        `<p class="form-hint">File SHA-256: ${esc(result.file_sha256.slice(0,16))}… · Reviewer: ${esc(result.reviewer_id)}</p>`+
        `<p class="form-hint">${esc(result.warning)}</p>`+
        `<div class="s81-import-list">${result.items.filter(x=>x.status!=='ready').slice(0,40).map(x=>`<p><b>${esc(x.case_id||'row '+x.row)}</b> · ${esc(x.status)} · ${esc(x.message)}</p>`).join('')||'All reviewed rows are ready for import.'}</div>`;
      $('#s82Commit').disabled=!result.ready_to_commit;
      s82SetFeedback(result.ready_to_commit?`Preview complete: ${n.ready} new votes can be imported. Check counts, then confirm.`:'Preview complete. Nothing to import or fix the shown errors before confirmation.',result.ready_to_commit?'success':'info');
    }catch(error){s82SetFeedback('Preview failed: '+error.message,'error');}
    finally{$('#s82Preview').disabled=false;}
  };
  $('#s82Commit').onclick=async()=>{
    if(!s82PreviewState||!s82PreviewState.ready_to_commit)return;
    const n=s82PreviewState.counts;
    if(!confirm(`Import ${n.ready} NEW human votes for ${s82PreviewState.reviewer_id}? ${n.existing} existing votes will be preserved. Imported votes cannot be claimed independently blinded.`))return;
    $('#s82Commit').disabled=true;$('#s82Preview').disabled=true;s82SetFeedback('Committing new votes in one PostgreSQL transaction…','loading');
    try{
      if(s82PreviewState.reviewer_id!==s81Id())throw new Error('Reviewer ID changed. Preview the file again.');
      const out=await request('/api/human-review/single/import/commit',{method:'POST',body:s82Payload('commit')});
      s82SetFeedback(`Import completed: ${out.imported} new votes, ${out.already_recorded} previously saved, ${out.empty} blank human rows. Your original votes remain unchanged.`,'success');
      s82Reset();await loadSingleReview(false);
    }catch(error){s82SetFeedback('Import NOT confirmed: '+error.message+'. Refresh preview before retrying.','error');}
    finally{$('#s82Preview').disabled=false;}
  };
  $('#s81Reload').onclick=()=>{state.s81Selected='';loadSingleReview(false)};
  $('#s81Identity').onchange=()=>{s82Reset();state.s81Selected='';$('#s81Report').innerHTML='<div class="empty">Identity changed. Compare only after selecting your original reviewer ID.</div>';loadSingleReview(false)};
  $('#s81Filter').onchange=s81RenderQueue;
  $('#s81Search').oninput=s81RenderQueue;
  $('#s81Form').onsubmit=s81SaveVote;
  $('#s81Next').onclick=s81NextUnreviewed;
  $('#s81Csv').onclick=()=>s81Export('csv');
  $('#s81Json').onclick=()=>s81Export('json');
  $('#s81Compare').onclick=s81Compare;
  $('#s81Queue').addEventListener('click',e=>{const hit=e.target.closest('[data-s81-case]');if(hit)s81Open(hit.dataset.s81Case)});

  $('#v7Queue').onclick=async()=>{
    if(state.v7Submitting)return;
    state.v7Submitting=true;
    const feedback=$('#v7QueueFeedback');feedback.textContent='Queueing holdout study…';feedback.className='benchmark-feedback loading';renderReliability();
    try{
      const resp=await request('/api/reliability/studies?repeats='+encodeURIComponent($('#v7Repeats').value),{method:'POST'});
      state.v7SelectedId=resp.study_id;state.v7Detail=null;
      feedback.textContent='Queued: '+resp.study_id+' · '+resp.repeats+' sequential runs. Progress appears below.';
      feedback.className='benchmark-feedback success';await refresh();tab('reliability');
    }catch(err){feedback.textContent='Not queued: '+err.message;feedback.className='benchmark-feedback error';await refresh();}
    finally{state.v7Submitting=false;renderReliability()}
  };
  $('#v7ExportMd').onclick=()=>downloadReliability('md');
  $('#v7ExportCsv').onclick=()=>downloadReliability('csv');
  $('#v7ExportJson').onclick=()=>downloadReliability('json');
  $('#v7ExportPredictions').onclick=()=>downloadReliability('predictions');
  $('#v9Refresh').onclick=loadResearchV9;
  $('#v9DownloadMd').onclick=()=>v9Download('md');
  $('#v9DownloadJson').onclick=()=>v9Download('json');
  $('#startBenchmark').onclick=async()=>{
    if(state.benchmarkSubmitting)return;
    state.benchmarkSubmitting=true;
    benchmarkFeedback('Sending POST /api/benchmarks/runs to FastAPI…','loading');
    renderBenchmarks();
    try{
      const controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),20000);
      let run;
      try{run=await request('/api/benchmarks/runs?profile='+encodeURIComponent($('#bmRunnerProfile').value),{method:'POST',signal:controller.signal})}
      finally{clearTimeout(timeout)}
      benchmarkFeedback('Queued successfully: '+run.id+' · Profile: '+run.runner_version+' · Status: '+run.status+'. Progress is shown in Benchmark history.','success');
      await refresh();
      tab('benchmarks');
    }catch(err){
      // A timed-out response could still have committed a run: refresh before retrying.
      const detail=err.name==='AbortError'?'Request timed out. Check Benchmark history before retrying; it may already be queued.':err.message;
      benchmarkFeedback('Request not confirmed: '+detail+'  |  Diagnostic: see .local/benchmark-worker.stderr.log','error');
      await refresh();
    }finally{
      state.benchmarkSubmitting=false;
      renderBenchmarks();
    }
  };
  $('#evaluationForm').onsubmit=async(e)=>{e.preventDefault();const form=e.currentTarget;const f=new FormData(form);const taskId=f.get('task_id');if(!taskId)return toast('Choose a finalized workflow first',true);const submit=form.querySelector('button[type=submit]');submit.disabled=true;submit.textContent='Evaluating…';try{const gold=String(f.get('gold_source_ids')||'').split(/[\s,;]+/).map(s=>s.trim()).filter(Boolean);const run=await request(`/api/evaluations/tasks/${encodeURIComponent(taskId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({use_llm_judge:f.get('use_llm_judge')==='on',gold_source_ids:gold})});toast(run.status==='completed'?'Evaluation saved':'Judge unavailable; see the saved error',run.status!=='completed');await refresh();tab('evaluations');inspectEvaluation(run.id)}catch(err){toast(err.message,true)}finally{submit.disabled=false;submit.textContent='Run evaluation'}};
  $('#knowledgeUploadForm').onsubmit=async(e)=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);const file=form.querySelector('input[type=file]').files[0];if(!file)return;const submit=form.querySelector('button[type=submit]');submit.disabled=true;submit.textContent='Indexing…';try{await request('/api/knowledge/upload',{method:'POST',body:data});form.reset();toast('Document indexed in the knowledge base');await refresh()}catch(err){toast(err.message,true)}finally{submit.disabled=false;submit.textContent='＋ Upload and index'}};
  $('#knowledgeSearchForm').onsubmit=async(e)=>{e.preventDefault();const form=e.currentTarget;const query=new FormData(form).get('query');const out=$('#knowledgeSearchResults');out.innerHTML='<div class="empty">Searching vector index…</div>';try{const data=await request('/api/knowledge/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,limit:8})});out.innerHTML=data.results.length?data.results.map(r=>`<article class="knowledge-result"><div class="knowledge-result-head"><strong>${esc(r.title||r.filename)}</strong><span class="panel-tag">SIMILARITY ${Number(r.score).toFixed(3)}</span></div><p class="subline">${esc(r.filename)}${r.page?` · page ${r.page}`:''} · chunk ${Number(r.chunk_index)+1}</p><p>${esc(r.text)}</p><code>${esc(r.source_id)}</code></article>`).join(''):'<div class="empty">No relevant source found above the configured similarity threshold.</div>'}catch(err){out.innerHTML=`<div class="empty knowledge-error">${esc(err.message)}</div>`}};
  document.addEventListener('click',async(e)=>{const t=e.target.closest('[data-inspect],[data-decision],[data-go],[data-knowledge-delete],[data-eval-inspect],[data-benchmark-inspect],[data-v7-inspect],[data-h8-case]');if(!t)return;if(t.dataset.h8Case)openHumanCase(t.dataset.h8Case);else if(t.dataset.inspect)inspect(t.dataset.inspect);else if(t.dataset.decision)decision(t.dataset.decision,t.dataset.approved==='true');else if(t.dataset.go)tab(t.dataset.go);else if(t.dataset.evalInspect)inspectEvaluation(t.dataset.evalInspect);else if(t.dataset.benchmarkInspect)inspectBenchmark(t.dataset.benchmarkInspect);else if(t.dataset.v7Inspect){state.v7SelectedId=t.dataset.v7Inspect;state.v7Detail=null;loadReliabilityDetail(true)}else if(t.dataset.knowledgeDelete){if(!confirm(`Delete indexed source "${t.dataset.knowledgeName}"?`))return;try{await request(`/api/knowledge/documents/${encodeURIComponent(t.dataset.knowledgeDelete)}`,{method:'DELETE'});toast('Knowledge source deleted');await refresh()}catch(err){toast(err.message,true)}}});
  $('#modalClose').onclick=closeModal;$('#modalBackdrop').addEventListener('click',(e)=>{if(e.target===$('#modalBackdrop'))closeModal()});document.addEventListener('keydown',(e)=>{if(e.key==='Escape')closeModal()});
  $('#verifyBtn').onclick=async()=>{try{const v=await request('/api/audit/verify');const el=$('#verifyResult');el.className='verify-result'+(v.valid?'':' error');el.textContent=v.valid?`✓ Integrity verified: ${v.entries_checked} events in the chain.`:`✕ Broken hash chain at event #${v.first_invalid_id}.`}catch(e){toast(e.message,true)}};
  fetch('/health').then(r=>r.json()).then(h=>{$('#healthText').textContent='API online — key required';$('#modeChip').textContent='MODE: '+String(h.llm_mode).toUpperCase()}).catch(()=>{$('#healthDot').classList.add('offline');$('#healthText').textContent='API unavailable'});
})();
