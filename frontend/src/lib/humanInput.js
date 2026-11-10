// 与后端 ClarificationMiddleware 的协议对应：v1 = 文本/选项，v2 = 表单。
export function clarificationOf(message) {
  const request = message.artifact?.human_input;
  if (message.role !== 'tool' || request?.kind !== 'human_input_request' || (request.version !== 1 && request.version !== 2) || !request.request_id || !request.question) return null;
  return request;
}

export function createClarificationResponse(request, text, option = null) {
  return {
    version: 1, kind: 'human_input_response', source: request.source,
    request_id: request.request_id, response_kind: option ? 'option' : 'text',
    ...(option ? { option_id: option.id } : {}), value: option ? option.value : text.trim(),
  };
}

// 从消息重建状态，不把“已回答”单独存到 localStorage。
export function clarificationState(messages) {
  const requests = new Map();
  const answers = new Map();
  for (const message of messages) {
    const visible = message.additionalKwargs?.hide_from_ui !== true;
    const request = visible ? clarificationOf(message) : null;
    if (request) requests.set(request.request_id, request);
    if (message.role !== 'user') continue;
    const response = message.additionalKwargs?.human_input_response;
    if (response?.kind === 'human_input_response' && requests.has(response.request_id)) {
      answers.set(response.request_id, { ...response, pending: Boolean(message.pending) });
    } else if (!response && visible && message.text?.trim()) {
      // 兼容旧页面：在普通输入框里发送的下一条消息，也视为回答最近的问题。
      const latest = [...requests.values()].reverse().find(item => !answers.has(item.request_id));
      if (latest) answers.set(latest.request_id, { ...createClarificationResponse(latest, message.text), pending: Boolean(message.pending) });
    }
  }
  const openRequest = [...requests.values()].reverse().find(item => !answers.has(item.request_id)) || null;
  return { answers, openRequest };
}

// ---------------------------------------------------------------------------
// v2 表单（form）支持。提交仍走 v1 的 response（response_kind: text），
// 只是 value 里带一段「可读摘要 + JSON 块」，见 buildFormSubmissionValue。
// ---------------------------------------------------------------------------

// JS Object.prototype 成员：表单值存在按字段名 key 的普通对象里，撞名会读到原型属性。
const RESERVED_FIELD_NAMES = new Set([
  '__proto__', 'constructor', 'prototype', 'toString', 'toLocaleString',
  'valueOf', 'hasOwnProperty', 'isPrototypeOf', 'propertyIsEnumerable',
  '__defineGetter__', '__defineSetter__', '__lookupGetter__', '__lookupSetter__',
]);

const FIELD_TYPES = new Set(['text', 'textarea', 'number', 'select', 'multi_select', 'checkbox']);

function isNonEmptyString(value) { return typeof value === 'string' && value.trim().length > 0; }

function parseFormOptions(value) {
  if (!Array.isArray(value)) return undefined;
  const options = [];
  for (const option of value) {
    if (!option || typeof option !== 'object') return undefined;
    if (!isNonEmptyString(option.id) || !isNonEmptyString(option.label) || !isNonEmptyString(option.value)) return undefined;
    options.push({ id: option.id, label: option.label, value: option.value });
  }
  return options;
}

// 校验并归一字段列表；任何一个字段坏了返回 undefined，调用方降级成纯文本卡片。
// 后端已经归一过一遍，这里再防一次「被重放/篡改的 artifact」。
export function parseFormFields(value) {
  if (!Array.isArray(value)) return undefined;
  const fields = [];
  const seen = new Set();
  for (const field of value) {
    if (!field || typeof field !== 'object') return undefined;
    const { name, label, type } = field;
    if (!isNonEmptyString(name) || RESERVED_FIELD_NAMES.has(name) || seen.has(name) || !isNonEmptyString(label) || !FIELD_TYPES.has(type)) return undefined;
    seen.add(name);
    const options = field.options === undefined ? undefined : parseFormOptions(field.options);
    if (field.options !== undefined && options === undefined) return undefined;
    if ((type === 'select' || type === 'multi_select') && (!options || !options.length)) return undefined;
    fields.push({
      name, label, type,
      required: field.required === true,
      ...(isNonEmptyString(field.placeholder) ? { placeholder: field.placeholder } : {}),
      ...(options ? { options } : {}),
    });
  }
  return fields;
}

// checkbox 默认 false：否则「没勾选」和「没答」分不清，提交时字段会凭空消失。
export function buildInitialFormValues(fields) {
  const values = {};
  for (const field of fields || []) {
    if (field.type === 'checkbox') values[field.name] = false;
  }
  return values;
}

function isEmptyFormValue(value) {
  if (value === undefined) return true;
  if (typeof value === 'string') return value.trim().length === 0;
  if (Array.isArray(value)) return value.length === 0;
  return false; // number / boolean 永远算「有值」（0 和 false 也是有效答案）
}

export function findMissingRequiredFields(fields, values) {
  const missing = [];
  for (const field of fields || []) {
    if (!field.required) continue;
    const value = values[field.name];
    // 必填的 checkbox 必须勾选（true 才算填了），所以 false 也算缺。
    // 注意和 buildFormSummary 的空值判断不同：那里 false 会显示成「否」。
    const empty = value === undefined
      || (typeof value === 'string' && !value.trim())
      || (Array.isArray(value) && !value.length)
      || value === false;
    if (empty) missing.push(field);
  }
  return missing;
}

function formatFormValue(value) {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

export function buildFormSummary(fields, values) {
  const parts = [];
  for (const field of fields || []) {
    const value = values[field.name];
    if (isEmptyFormValue(value)) continue;
    parts.push(`${field.label}: ${formatFormValue(value)}`);
  }
  return parts.join('; ');
}

export function buildFormSubmissionValue(fields, values) {
  // 只靠「label: value; ...」会有歧义（label 里可能带分号），所以末尾再附一整块
  // 按字段名 key 的 JSON，模型想精确取值就解析这一块，不会认错字段。
  const record = {};
  for (const field of fields || []) {
    const value = values[field.name];
    if (isEmptyFormValue(value)) continue;
    record[field.name] = value;
  }
  return `${buildFormSummary(fields, values)} [values: ${JSON.stringify(record)}]`;
}

// 提交时的字段 JSON 已经保存在回答消息中，刷新后从这里恢复表单值。
// 普通聊天回答没有这个结构，返回 null，保留原有的文字回答方式。
export function restoreFormValues(fields, value) {
  const marker = ' [values: ';
  if (typeof value !== 'string' || !value.endsWith(']')) return null;
  for (let index = value.indexOf(marker); index >= 0; index = value.indexOf(marker, index + marker.length)) {
    let record;
    try { record = JSON.parse(value.slice(index + marker.length, -1)); }
    catch { continue; }
    if (!record || typeof record !== 'object' || Array.isArray(record)) continue;
    const values = buildInitialFormValues(fields);
    for (const field of fields) {
      if (!Object.hasOwn(record, field.name)) continue;
      const item = record[field.name];
      if (field.type === 'checkbox' ? typeof item === 'boolean'
        : field.type === 'multi_select' ? Array.isArray(item) && item.every(option => typeof option === 'string')
          : typeof item === 'string' || (field.type === 'number' && typeof item === 'number' && Number.isFinite(item))) {
        values[field.name] = item;
      }
    }
    return values;
  }
  return null;
}
