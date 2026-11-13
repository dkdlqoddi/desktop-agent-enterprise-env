const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld(
  'privateAgent',
  Object.freeze({
    request: (method, params = {}) => ipcRenderer.invoke('agent:request', method, params),
    onAssistantOpen: (callback) => {
      const listener = () => callback();
      ipcRenderer.on('assistant:open', listener);
      return () => ipcRenderer.removeListener('assistant:open', listener);
    },
    // The quick-access shortcut or the tray's 채팅 열기 (electron/quick-access.cjs): show the chat with the caret in its
    // message box. It carries nothing, and the callback is given nothing.
    onChatFocus: (callback) => {
      const listener = () => callback();
      ipcRenderer.on('chat:focus', listener);
      return () => ipcRenderer.removeListener('chat:focus', listener);
    },
    onScheduleEvent: (callback) => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on('schedule:event', listener);
      return () => ipcRenderer.removeListener('schedule:event', listener);
    },
    // An automatic routine's run ended, or its notification was clicked.
    onRoutineEvent: (callback) => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on('routine:event', listener);
      return () => ipcRenderer.removeListener('routine:event', listener);
    },
    onImportNotice: (callback) => {
      const listener = (_event, text) => callback(text);
      ipcRenderer.on('import:notice', listener);
      return () => ipcRenderer.removeListener('import:notice', listener);
    },
    // An answer as the LLM writes it, for a question asked with stream (assistant.ask): its parts (type 'delta') and
    // its end (type 'done'), each naming the request_id and conversation_id it belongs to.
    onAssistantDelta: (callback) => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on('assistant:delta', listener);
      return () => ipcRenderer.removeListener('assistant:delta', listener);
    },
    // A background job (an import, the AI analysis) or the automatic routines changed while the main process followed
    // them (electron/job-progress.cjs): { seq, kind, job } or { seq, kind: 'routines', routines }.
    onJobProgress: (callback) => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on('job:progress', listener);
      return () => ipcRenderer.removeListener('job:progress', listener);
    },
    // The local API's commands to the screen (open a document, read the screen, …), each answered once.
    onControl: (handler) => {
      const answer = async (command) => {
        let reply;
        try {
          reply = { id: command.id, ok: true, result: await handler(command.type, command.params) };
        } catch (error) {
          reply = {
            id: command.id,
            ok: false,
            error: error?.message || '앱 화면에서 처리하지 못했습니다.',
          };
        }
        ipcRenderer.send('control:result', reply);
      };
      // A command that fails is answered as such above; nothing reads the promise.
      const listener = (_event, command) => void answer(command);
      ipcRenderer.on('control:command', listener);
      ipcRenderer.send('control:ready');
      return () => ipcRenderer.removeListener('control:command', listener);
    },
    // An AI tool changed data through the local API; the screens reload it.
    onApiChange: (callback) => {
      const listener = () => callback();
      ipcRenderer.on('api:changed', listener);
      return () => ipcRenderer.removeListener('api:changed', listener);
    },
    // An action of the chat agent (a chat turn, a card the user approved, a messenger's conversation) changed tasks,
    // documents or routines: the names of those lists, which the screens read again.
    onAgentChange: (callback) => {
      const listener = (_event, tags) =>
        callback(Array.isArray(tags) ? tags.filter((tag) => typeof tag === 'string') : []);
      ipcRenderer.on('agent:changed', listener);
      return () => ipcRenderer.removeListener('agent:changed', listener);
    },
  }),
);
