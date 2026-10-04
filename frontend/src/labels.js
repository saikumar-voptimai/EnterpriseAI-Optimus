// Human-readable labels for assistant internals. The interface describes what the
// assistant is doing, never internal tool, model or infrastructure names.

export const sentence = value => {
  const text = String(value ?? '').replaceAll('_', ' ').replaceAll('-', ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
};

const TOOLS = {
  knowledge_search: 'Knowledge base',
  list_incidents: 'Incidents',
  find_reports: 'Reports',
  calendar_events: 'Calendar',
  list_data_connections: 'Plant data sources',
  describe_timeseries: 'Plant data catalogue',
  read_timeseries: 'Plant data readings',
  describe_table: 'Database catalogue',
  query_table: 'Database records',
  list_folder_files: 'Shared spreadsheets',
  read_spreadsheet: 'Spreadsheet data',
  calculate_statistics: 'Statistics',
  save_personal_note: 'Save a note',
  create_personal_reminder: 'Reminders',
};

const TOOL_ACTIVITY = {
  knowledge_search: 'Searching the knowledge base',
  list_incidents: 'Reviewing incidents',
  find_reports: 'Looking up reports',
  calendar_events: 'Checking the calendar',
  list_data_connections: 'Finding plant data sources',
  describe_timeseries: 'Exploring plant data',
  read_timeseries: 'Reading plant data',
  describe_table: 'Exploring database tables',
  query_table: 'Reading database records',
  list_folder_files: 'Looking through shared spreadsheets',
  read_spreadsheet: 'Reading a spreadsheet',
  calculate_statistics: 'Calculating statistics',
  save_personal_note: 'Saving a note',
  create_personal_reminder: 'Creating a reminder',
};

const STAGES = {
  queued: 'Queued',
  starting: 'Starting',
  running: 'Working',
  thinking: 'Thinking',
  tool: 'Using a capability',
  validating: 'Checking sources',
  finalizing: 'Finishing',
  completed: 'Completed',
  succeeded: 'Completed',
  failed: 'Could not complete',
  cancelled: 'Cancelled',
  cancel_requested: 'Cancelling',
  retrying: 'Retrying',
  waiting: 'Waiting',
};

const SKILLS = {
  'operational-analysis': 'Operational analysis',
  'management-briefing': 'Management briefing',
  'personal-assistant': 'Personal assistant',
};

const PREFERENCES = {
  language: 'Language',
  timezone: 'Time zone',
  units: 'Units',
  response_style: 'Response style',
  personal_instructions: 'Personal instructions',
};

export const toolLabel = name => TOOLS[name] || 'Assistant capability';
export const stageLabel = stage => STAGES[stage] || sentence(stage);
export const skillLabel = name => SKILLS[name] || sentence(name);
export const preferenceLabel = key => PREFERENCES[key] || sentence(key);

// Values keep their own casing when it carries meaning (SI, Asia/Kolkata);
// lower-case words get a capital first letter.
export const preferenceValue = (key, value) => {
  const text = String(value ?? '').trim();
  if (key === 'timezone') return text.replaceAll('_', ' ');
  return /^[a-z]/.test(text) ? sentence(text) : text;
};

export function stepText(event) {
  const detail = event?.detail && typeof event.detail === 'object' ? event.detail : {};
  if (event?.stage === 'tool') return TOOL_ACTIVITY[detail.name] || 'Using a capability';
  if (event?.stage === 'thinking' && detail.step > 1) return `Thinking (step ${detail.step})`;
  if (event?.stage === 'starting' && detail.attempt > 1) return `Retrying (attempt ${detail.attempt})`;
  if (event?.stage === 'completed' && Number.isInteger(detail.source_count))
    return detail.source_count
      ? `Completed · ${detail.source_count} source${detail.source_count === 1 ? '' : 's'}`
      : 'Completed';
  return stageLabel(event?.stage);
}

const SOURCE_TYPES = {
  document: 'Document',
  incident: 'Incident',
  report: 'Report',
  connection: 'Plant data',
  calendar: 'Calendar',
  note: 'Note',
  publication: 'Published summary',
};
export const sourceLabel = ref => ref?.title || ref?.filename || SOURCE_TYPES[ref?.type] || 'Source';
