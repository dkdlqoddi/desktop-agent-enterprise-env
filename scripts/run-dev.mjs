import { npmCiSteps, runWorkflow } from './workflow.mjs';

runWorkflow([
  ...npmCiSteps(),
  ['run', 'setup:python'],
  ['run', 'setup:browser'],
  ['run', 'setup:electron'],
  ['run', 'dev'],
]);
