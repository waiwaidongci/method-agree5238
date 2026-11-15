export interface User{id:number;name:string;email:string;role:string}
export interface Instrument{id:number;code:string;name:string;serial:string;unit:string;lower:number;upper:number}
export interface Dataset{id:number;name:string;reference_id:number;candidate_id:number;source_file:string;note:string;row_count:number}
export interface Reading{id:number;dataset_id:number;sample_code:string;reference_value:number;candidate_value:number}
export interface Point{code:string;x:number;y:number;mean:number;diff:number}
export interface Result{n:number;intercept:number;slope:number;intercept_ci:number[];slope_ci:number[];bias:number;sd_difference:number;loa_lower:number;loa_upper:number;bias_ci:number[];lower_ci:number[];upper_ci:number[];correlation:number;spread_ratio:number;heteroscedastic_warning:boolean;bootstrap_success:number;bootstrap_failed:number;bootstrap_seed:number;fingerprint:string;x_range:number[];y_range:number[];config:{lambda_ratio:number;allowance:number;range_low:number;range_high:number;bootstrap_count:number};raw_eligible:boolean;instrument_codes:string[];points:Point[]}
export interface Run{id:number;name:string;dataset_id:number;lambda_ratio:number;allowance:number;range_low:number;range_high:number;bootstrap_count:number;status:string;error:string;created_at:string;result:Partial<Result>}
export interface Qualification{id:number;title:string;run_id:number;mode:string;scope_low:number;scope_high:number;note:string;status:string;approved_by:string;created_at:string}
export interface Audit{id:number;actor:string;action:string;status_code:number;created_at:string}
export type MergeSide='reference'|'candidate';
export type MergeEventKind='reading'|'withdrawal';
export type MergeEventState='staged'|'applied'|'quarantined'|'duplicate';
export type MergeSpecimenStatus='waiting'|'partial'|'active'|'withdrawn'|'blocked';
export interface MergeQuarantineItem{version:number;kind:MergeEventKind;value:number|null;reason:string}
export interface MergeSideState{applied_version:number|null;kind:MergeEventKind|null;value:number|null;staged_versions:number[];quarantined:MergeQuarantineItem[]}
export interface MergeSpecimen{id:number;sample_code:string;status:MergeSpecimenStatus;block_reason:string;reference:MergeSideState;candidate:MergeSideState;updated_at:string}
export interface MergeChannelCounters{specimen_total:number;complete:number;partial:number;blocked:number;withdrawn:number;waiting:number;staged_events:number}
export type MergeChannelSummary={id:number;name:string;note:string;reference_id:number;candidate_id:number;reference_code:string;candidate_code:string}&MergeChannelCounters
export interface MergeEventRow{id:number;side:MergeSide;sample_code:string;version:number;kind:MergeEventKind;value:number|null;state:MergeEventState;reason:string;received_at:string}
export interface SampleSetExclusion{sample_code:string;reason_code:'conflict'|'missing_version'|'missing_side';reason:string;detail:Record<string,MergeSideState>}
export interface SampleSetRow{sample_code:string;reference_value:number;candidate_value:number;ref_version:number;cand_version:number}
export interface SampleSetSummary{note:string;frozen_at:string;complete:number;excluded:number;reasons:Record<string,string>;counts:{conflict:number;missing_version:number;missing_side:number}}
export interface SampleSetBrief{id:number;name:string;channel_id:number;frozen_at:string;created_by:string;complete_count:number;excluded_count:number;summary:SampleSetSummary}
export interface SampleSetDetail extends SampleSetBrief{rows:SampleSetRow[];exclusions:SampleSetExclusion[]}
export interface MergeChannelDetail extends MergeChannelSummary{unit:string;counters:MergeChannelCounters;specimens:MergeSpecimen[];sample_sets:SampleSetBrief[];can_freeze:boolean}
export interface MergeEventInput{side:MergeSide;sample_code:string;version:number;kind:MergeEventKind;value:number|null}
export interface IngestVerdict{sample_code:string;side:MergeSide;version:number;kind:MergeEventKind;verdict:string;specimen:MergeSpecimen}
