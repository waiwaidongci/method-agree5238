import {request,post} from './client';
import type {
  MergeChannelSummary,MergeChannelDetail,MergeEventRow,MergeEventInput,
  IngestVerdict,SampleSetBrief,SampleSetDetail,
} from '../types/domain';

export const getMergeChannels=()=>request<MergeChannelSummary[]>('/merge/channels');
export const createMergeChannel=(data:{name:string;reference_id:number;candidate_id:number;note:string})=>
  post<MergeChannelDetail>('/merge/channels',data);
export const getMergeChannel=(id:number)=>request<MergeChannelDetail>(`/merge/channels/${id}`);
export const postMergeEvent=(channelId:number,data:MergeEventInput)=>
  post<IngestVerdict>(`/merge/channels/${channelId}/events`,data);
export const postMergeBatch=(channelId:number,events:MergeEventInput[])=>
  post<{accepted:IngestVerdict[];errors:unknown[]}>(`/merge/channels/${channelId}/events/batch`,{events});
export const getMergeEvents=(channelId:number,search='',state='',offset=0,limit=50)=>
  request<{total:number;rows:MergeEventRow[]}>(`/merge/channels/${channelId}/events?search=${encodeURIComponent(search)}&state=${state}&offset=${offset}&limit=${limit}`);
export const freezeSampleSet=(channelId:number,data:{name:string;note:string})=>
  post<SampleSetDetail>(`/merge/channels/${channelId}/freeze`,data);
export const getSampleSets=(channelId?:number)=>
  request<SampleSetBrief[]>(`/merge/sample-sets${channelId?`?channel_id=${channelId}`:''}`);
export const getSampleSet=(id:number)=>request<SampleSetDetail>(`/merge/sample-sets/${id}`);
