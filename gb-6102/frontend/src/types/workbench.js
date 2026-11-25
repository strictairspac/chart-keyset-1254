/**
 * @typedef {{id:number, code:string, title:string, description:string, payload:Object|null,
 *   created_by:string, sealed:boolean, sealed_at:number|null, sealed_by:string|null,
 *   retention_state:string, retention_requested_at:number|null, retention_cleaned_at:number|null}} Case
 * @typedef {{id:number, case_id:number, case_title:string, status:string, parameter:number, result:Object|null}} Run
 * @typedef {{run_id:number, actor:string, note:string, created_at:number}} Review
 */

/** Reject a malformed configuration before rendering its navigation. */
export function validateConfig(config) {
  if (!config || !Array.isArray(config.modules) || config.modules.length !== 4 ||
      !Number.isInteger(config.default_parameter) || !config.title) {
    throw new Error("工作台配置不完整");
  }
  return config;
}
