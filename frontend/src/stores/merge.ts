import {defineStore} from 'pinia';
import {ref} from 'vue';
import {
  getMergeChannels,getMergeChannel,postMergeEvent,postMergeBatch,
  freezeSampleSet,getMergeEvents,getSampleSet,
} from '../api/merge';
import type {
  MergeChannelSummary,MergeChannelDetail,MergeEventInput,IngestVerdict,
  MergeEventRow,SampleSetDetail,
} from '../types/domain';

export const useMerge=defineStore('merge',()=>{
  const channels=ref<MergeChannelSummary[]>([]);
  const detail=ref<MergeChannelDetail|null>(null);
  const events=ref<MergeEventRow[]>([]);
  const eventTotal=ref(0);
  const activeSet=ref<SampleSetDetail|null>(null);

  async function loadChannels(){channels.value=await getMergeChannels();}
  async function loadDetail(id:number){detail.value=await getMergeChannel(id);}
  async function loadEvents(id:number,search='',state='',offset=0){
    const r=await getMergeEvents(id,search,state,offset);
    events.value=r.rows;eventTotal.value=r.total;
  }
  async function ingest(id:number,data:MergeEventInput):Promise<IngestVerdict>{
    const r=await postMergeEvent(id,data);await loadDetail(id);return r;
  }
  async function ingestMany(id:number,rows:MergeEventInput[]){
    const r=await postMergeBatch(id,rows);await loadDetail(id);return r;
  }
  async function freeze(id:number,name:string,note:string){
    activeSet.value=await freezeSampleSet(id,{name,note});
    await loadDetail(id);
    await loadChannels();
    return activeSet.value;
  }
  async function openSet(setId:number){activeSet.value=await getSampleSet(setId);}
  function closeSet(){activeSet.value=null;}
  return {
    channels,detail,events,eventTotal,activeSet,
    loadChannels,loadDetail,loadEvents,ingest,ingestMany,freeze,openSet,closeSet,
  };
});
