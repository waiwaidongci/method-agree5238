export const roles:Record<string,string>={admin:'实验室负责人',analyst:'测量工程师',viewer:'设备观察员'};
export const statuses:Record<string,string>={queued:'已排队',running:'计算中',completed:'已归档',failed:'未完成',draft:'待复核',approved:'已批准'};
export const modes:Record<string,string>={raw:'原值互换',calibration_only:'仅供校准研究'};
export const mergeSpecimenStatuses:Record<string,string>={waiting:'待回传',partial:'单侧已到',active:'完整配对',withdrawn:'已撤回',blocked:'冲突阻断'};
export const mergeEventStates:Record<string,string>={staged:'暂存待补',applied:'已生效',quarantined:'已隔离',duplicate:'重复忽略'};
export const mergeSides:Record<string,string>={reference:'参考侧',candidate:'候选侧'};
export const exclusionReasons:Record<string,string>={conflict:'同版异值冲突',missing_version:'缺版（暂存待补）',missing_side:'缺侧 / 撤回'};
