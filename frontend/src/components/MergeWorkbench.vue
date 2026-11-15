<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue';
import {useLab} from '../stores/laboratory';
import {useMerge} from '../stores/merge';
import {useAuth} from '../stores/auth';
import {useTask} from '../hooks/useTask';
import {createMergeChannel} from '../api/merge';
import ErrorNotice from './common/ErrorNotice.vue';
import EmptyState from './common/EmptyState.vue';
import {mergeSpecimenStatuses,mergeEventStates,mergeSides,exclusionReasons} from '../constants/enums';
import {number} from '../utils/format';
import type {MergeEventInput,MergeSpecimen} from '../types/domain';

const lab=useLab(),merge=useMerge(),auth=useAuth(),{busy,error,perform}=useTask();
const selectedId=ref(0);
const showChannelForm=ref(false);
const channelForm=ref({name:'',reference_id:0,candidate_id:0,note:''});
const eventForm=ref({side:'reference' as const,sample_code:'',version:1,kind:'reading' as kindT,value:null as number|null});
type kindT='reading'|'withdrawal';
const batchText=ref('');
const verdict=ref('');
const search=ref(''),eventState=ref(''),eventOffset=ref(0);
const freezeForm=ref({name:'',note:''});

const detail=computed(()=>merge.detail);

onMounted(()=>perform(async()=>{
  await Promise.all([lab.load(),merge.loadChannels()]);
  channelForm.value.reference_id=lab.instruments[0]?.id||0;
  channelForm.value.candidate_id=lab.instruments[1]?.id||0;
  selectedId.value=merge.channels[0]?.id||0;
  if(selectedId.value)await open(selectedId.value);
}));
watch(selectedId,async id=>{if(id)await perform(()=>open(id));});

async function open(id:number){
  search.value='';eventState.value='';eventOffset.value=0;
  await Promise.all([merge.loadDetail(id),merge.loadEvents(id,search.value,eventState.value,eventOffset.value)]);
  merge.closeSet();
}
async function reloadEvents(){await merge.loadEvents(selectedId.value,search.value,eventState.value,eventOffset.value);}
function createChannel(){perform(async()=>{
  const ch=await createMergeChannel(channelForm.value);
  await merge.loadChannels();
  selectedId.value=ch.id;
  showChannelForm.value=false;
  channelForm.value={name:'',reference_id:lab.instruments[0]?.id||0,candidate_id:lab.instruments[1]?.id||0,note:''};
});}
function sendEvent(){perform(async()=>{
  const r=await merge.ingest(selectedId.value,{...eventForm.value,value:eventForm.value.kind==='withdrawal'?null:eventForm.value.value});
  verdict.value=`${r.sample_code} ${mergeSides[r.side]} v${r.version}：${verdictLabel(r.verdict)}`;
  await reloadEvents();
  eventForm.value.sample_code='';
});}
function verdictLabel(v:string){
  return ({applied:'已连续推进生效',staged:'乱序暂存，等待前版补齐',duplicate:'重复回传，未重复计入',
    conflict:'同版异值，试片已阻断',quarantined:'已隔离（试片阻断中）',stale:'旧版回传，不复活撤回值'} as Record<string,string>)[v]||v;
}
function parseBatch():MergeEventInput[]{
  const lines=batchText.value.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
  return lines.map(line=>{
    const [side,sample_code,rawVersion,kindOrValue,maybeValue]=line.split(',').map(x=>x?.trim());
    if(!side||!sample_code||!rawVersion)throw new Error(`行格式不足：${line}（应为 侧,试片号,版本,reading,数值 或 侧,试片号,版本,withdrawal）`);
    if(!(side==='reference'||side==='candidate'))throw new Error(`侧必须为 reference/candidate：${line}`);
    const version=Number(rawVersion);
    if(!Number.isInteger(version)||version<1)throw new Error(`版本须为≥1整数：${line}`);
    if(kindOrValue==='withdrawal'){
      if(maybeValue)throw new Error(`撤回事件不得带数值：${line}`);
      return {side,sample_code,version,kind:'withdrawal',value:null};
    }
    if(kindOrValue!=='reading')throw new Error(`类型须为 reading/withdrawal：${line}`);
    const value=Number(maybeValue);
    if(!Number.isFinite(value))throw new Error(`读数须为有限数值：${line}`);
    return {side,sample_code,version,kind:'reading',value};
  });
}
function sendBatch(){perform(async()=>{
  const rows=parseBatch();
  if(!rows.length)throw new Error('批量内容为空');
  const r=await merge.ingestMany(selectedId.value,rows);
  verdict.value=`批量受理 ${r.accepted.length} 条，已按试片号与版本连续归并`;
  batchText.value='';
  await reloadEvents();
});}
function findEvents(){eventOffset.value=0;perform(reloadEvents);}
function pageEvents(delta:number){eventOffset.value=Math.max(0,eventOffset.value+delta);perform(reloadEvents);}
function doFreeze(){perform(async()=>{
  if(!freezeForm.value.name)throw new Error('请填写研究样本集名称');
  await merge.freeze(selectedId.value,freezeForm.value.name,freezeForm.value.note);
  freezeForm.value={name:'',note:''};
});}
function sideText(s:MergeSpecimen,side:'reference'|'candidate'){
  const st=s[side];
  if(st.kind==='withdrawal')return `v${st.applied_version} 撤回`;
  if(st.kind==='reading')return `v${st.applied_version} = ${number(st.value??0,4)}`;
  return '—';
}
function exportUrl(id:number){return `/api/merge/sample-sets/${id}/export`;}
function authHeader(){return {Authorization:'Bearer '+localStorage.getItem('optics-token')};}
function downloadSet(id:number){
  // 带鉴权头下载，保留浏览器原生文件保存
  fetch(exportUrl(id),{headers:authHeader()}).then(async r=>{
    if(!r.ok)throw new Error('导出失败');
    const blob=await r.blob();const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download=`sample-set-${id}.csv`;a.click();
    URL.revokeObjectURL(url);
  }).catch(e=>{error.value=e.message;});
}
</script>

<template>
  <section class="merge-workbench">
    <div class="section-heading">
      <h2>双侧增量归并</h2>
      <span>INCREMENTAL TWO-SIDE MERGE / 版本从 v1 连续递增</span>
    </div>
    <ErrorNotice :message="error" @retry="()=>perform(()=>open(selectedId))"/>
    <p v-if="verdict" class="success">{{verdict}}</p>

    <div class="merge-toolbar">
      <label>归并通道
        <select v-model.number="selectedId">
          <option v-for="c in merge.channels" :key="c.id" :value="c.id">
            {{c.name}}（{{c.reference_code}} ⇄ {{c.candidate_code}}）
          </option>
        </select>
      </label>
      <button v-if="auth.canEdit" class="outline" @click="showChannelForm=!showChannelForm">
        {{showChannelForm?'取消':'新建归并通道 +'}}
      </button>
    </div>

    <form v-if="showChannelForm" class="entry-form" @submit.prevent="createChannel">
      <h2>新建双侧归并通道</h2>
      <div class="form-grid">
        <label>通道名称<input v-model="channelForm.name" required minlength="2" placeholder="如 N03 · 试片增量回传"></label>
        <label>参考仪器 X<select v-model.number="channelForm.reference_id"><option v-for="i in lab.instruments" :key="i.id" :value="i.id">{{i.code}}</option></select></label>
        <label>候选仪器 Y<select v-model.number="channelForm.candidate_id"><option v-for="i in lab.instruments" :key="i.id" :value="i.id">{{i.code}}</option></select></label>
      </div>
      <label>说明<input v-model="channelForm.note" maxlength="600"></label>
      <button class="primary" :disabled="busy">建立通道 →</button>
    </form>

    <EmptyState v-if="!merge.channels.length" title="还没有归并通道" text="新建通道后，两台仪器即可分别回传读数或撤回，系统按试片号与版本自动归并。"/>

    <template v-if="detail">
      <div class="merge-counters">
        <div><b>{{detail.counters.complete}}</b><span>完整配对</span></div>
        <div><b>{{detail.counters.partial}}</b><span>单侧已到</span></div>
        <div><b>{{detail.counters.staged_events}}</b><span>暂存待补</span></div>
        <div><b class="plum">{{detail.counters.blocked}}</b><span>冲突阻断</span></div>
        <div><b>{{detail.counters.withdrawn}}</b><span>撤回</span></div>
      </div>

      <div v-if="auth.canEdit" class="merge-input-grid">
        <form class="research-form" @submit.prevent="sendEvent">
          <h2>单条回传</h2>
          <div class="form-grid two">
            <label>仪器侧<select v-model="eventForm.side"><option value="reference">参考侧（{{detail.reference_code}}）</option><option value="candidate">候选侧（{{detail.candidate_code}}）</option></select></label>
            <label>试片号<input v-model="eventForm.sample_code" required maxlength="80" placeholder="如 CG1-024"></label>
            <label>版本（从 1 连续递增）<input type="number" min="1" step="1" v-model.number="eventForm.version" required></label>
            <label>事件类型<select v-model="eventForm.kind"><option value="reading">读数</option><option value="withdrawal">撤回</option></select></label>
          </div>
          <label v-if="eventForm.kind==='reading'">读数 / {{detail.unit}}<input type="number" step="0.0001" v-model.number="eventForm.value" required></label>
          <p class="input-note">乱序版本先暂存；同版异值将隔离并阻断该试片；重复回传不重复计入；旧版回传不复活撤回值。</p>
          <button class="primary" :disabled="busy">提交回传 →</button>
        </form>

        <form class="research-form" @submit.prevent="sendBatch">
          <h2>批量粘贴</h2>
          <label>每行：侧(reference/candidate),试片号,版本,reading,数值 或 侧,试片号,版本,withdrawal
            <textarea v-model="batchText" rows="6" placeholder="reference,CG1-024,2,reading,51.2&#10;reference,CG1-024,1,reading,50.8&#10;candidate,CG1-024,1,withdrawal"></textarea>
          </label>
          <p class="input-note">整批同事务顺序归并，批内乱序可连续推进；任一行非法则整批不入库。</p>
          <button class="primary" :disabled="busy">批量归并 →</button>
        </form>
      </div>

      <div class="section-heading" style="margin-top:30px">
        <h2>试片归并状态</h2>
        <span>{{detail.specimens.length}} 片 · 冻结只取同一时点的双侧状态</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>试片号</th><th>状态</th><th>参考侧 {{detail.reference_code}}</th><th>候选侧 {{detail.candidate_code}}</th><th>待补 / 隔离依据</th></tr></thead>
          <tbody>
            <tr v-for="s in detail.specimens" :key="s.id" :class="{blockedRow:s.status==='blocked'}">
              <td><b>{{s.sample_code}}</b></td>
              <td><span class="status" :class="s.status">{{mergeSpecimenStatuses[s.status]}}</span></td>
              <td>{{sideText(s,'reference')}}<small v-if="s.reference.staged_versions.length">暂存版本：v{{s.reference.staged_versions.join('、v')}}</small></td>
              <td>{{sideText(s,'candidate')}}<small v-if="s.candidate.staged_versions.length">暂存版本：v{{s.candidate.staged_versions.join('、v')}}</small></td>
              <td class="plum" style="white-space:normal;max-width:340px">
                <template v-if="s.block_reason">{{s.block_reason}}</template>
                <template v-else-if="s.reference.quarantined.length||s.candidate.quarantined.length">
                  <small v-for="(q,i) in [...s.reference.quarantined,...s.candidate.quarantined]" :key="i">v{{q.version}}：{{q.reason}}</small>
                </template>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-if="!detail.specimens.length" title="暂无试片" text="提交第一条回传后，这里会出现试片级归并状态。"/>

      <div class="section-heading" style="margin-top:30px">
        <h2>回传事件流</h2>
        <span>暂存 / 生效 / 隔离 / 重复，全部留痕，中断恢复后仍可追溯</span>
      </div>
      <div class="data-toolbar">
        <form @submit.prevent="findEvents" style="display:flex;gap:7px">
          <input v-model="search" placeholder="按试片号筛选" style="max-width:220px">
          <select v-model="eventState" @change="findEvents" style="max-width:160px">
            <option value="">全部状态</option>
            <option v-for="(label,key) in mergeEventStates" :key="key" :value="key">{{label}}</option>
          </select>
          <button class="outline">查找</button>
        </form>
        <span class="muted">共 {{merge.eventTotal}} 条</span>
      </div>      <div class="table-wrap">
        <table>
          <thead><tr><th>试片号</th><th>侧</th><th>版本</th><th>类型</th><th>数值</th><th>状态</th><th>原因</th></tr></thead>
          <tbody>
            <tr v-for="e in merge.events" :key="e.id">
              <td><b>{{e.sample_code}}</b></td>
              <td>{{mergeSides[e.side]}}</td>
              <td>v{{e.version}}</td>
              <td>{{e.kind==='withdrawal'?'撤回':'读数'}}</td>
              <td>{{e.kind==='withdrawal'?'—':number(e.value??0,4)}}</td>
              <td><span class="status" :class="e.state">{{mergeEventStates[e.state]}}</span></td>
              <td class="muted" style="white-space:normal;max-width:320px">{{e.reason||'—'}}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>{{eventOffset+1}}—{{Math.min(eventOffset+50,merge.eventTotal)}} / {{merge.eventTotal}}</span>
        <div>
          <button :disabled="eventOffset===0" @click="pageEvents(-50)">← 前50条</button>
          <button :disabled="eventOffset+50>=merge.eventTotal" @click="pageEvents(50)">后50条 →</button>
        </div>
      </div>

      <div class="freeze-grid">
        <form v-if="auth.canEdit" class="research-form" @submit.prevent="doFreeze">
          <h2>冻结当前完整配对为研究样本集</h2>
          <label>样本集名称<input v-model="freezeForm.name" required minlength="2" placeholder="如 N03 · 晨间冻结 R1"></label>
          <label>研究说明<input v-model="freezeForm.note" maxlength="600"></label>
          <p class="input-note">冻结在单事务加锁下完成，与回传并发时每份样本集取同一时点的双侧状态；冻结后的补传不改变旧研究。缺侧、缺版、冲突试片逐条列明原因。</p>
          <button class="primary" :disabled="busy||!detail.can_freeze">冻结样本集 →</button>
          <p v-if="!detail.can_freeze" class="input-note plum">当前没有完整配对，暂不可冻结。</p>
        </form>

        <div class="sample-set-list">
          <div class="section-heading"><h2>已冻结研究样本集</h2><span>不可变快照</span></div>
          <article v-for="x in detail.sample_sets" :key="x.id" class="sample-set-card">
            <div class="file-heading">
              <h3>{{x.name}} <small class="muted">#{{x.id}}</small></h3>
              <span class="status approved">已冻结</span>
            </div>
            <p class="muted">{{x.frozen_at}} · {{x.created_by}} · 完整 {{x.complete_count}} 对 / 排除 {{x.excluded_count}} 片</p>
            <p class="muted">冲突 {{x.summary.counts.conflict}} · 缺版 {{x.summary.counts.missing_version}} · 缺侧/撤回 {{x.summary.counts.missing_side}}</p>
            <footer style="display:flex;gap:10px;margin-top:10px">
              <button class="outline" @click="merge.openSet(x.id)">查看明细</button>
              <button class="outline" @click="downloadSet(x.id)">下载 CSV</button>
            </footer>
          </article>
          <EmptyState v-if="!detail.sample_sets.length" title="尚无冻结样本集" text="冻结后可在此查看入选配对与全部排除原因。"/>
        </div>
      </div>

      <div v-if="merge.activeSet" class="sample-set-detail">
        <div class="file-heading">
          <h2>样本集明细：{{merge.activeSet.name}}</h2>
          <button class="outline" @click="merge.closeSet()">关闭</button>
        </div>
        <p class="source-line">冻结时点 {{merge.activeSet.frozen_at}} · 后续补传不改变本样本集</p>
        <h3>入选完整配对（{{merge.activeSet.rows.length}}）</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>试片号</th><th>参考读数</th><th>参考版本</th><th>候选读数</th><th>候选版本</th></tr></thead>
            <tbody>
              <tr v-for="r in merge.activeSet.rows" :key="r.sample_code">
                <td><b>{{r.sample_code}}</b></td>
                <td>{{number(r.reference_value,4)}}</td><td>v{{r.ref_version}}</td>
                <td>{{number(r.candidate_value,4)}}</td><td>v{{r.cand_version}}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <h3 style="margin-top:22px">排除试片与原因（{{merge.activeSet.exclusions.length}}）</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>试片号</th><th>原因类别</th><th>具体依据</th></tr></thead>
            <tbody>
              <tr v-for="x in merge.activeSet.exclusions" :key="x.sample_code">
                <td><b>{{x.sample_code}}</b></td>
                <td><span class="status" :class="x.reason_code==='conflict'?'failed':'draft'">{{exclusionReasons[x.reason_code]}}</span></td>
                <td class="muted" style="white-space:normal;max-width:520px">{{x.reason}}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>
