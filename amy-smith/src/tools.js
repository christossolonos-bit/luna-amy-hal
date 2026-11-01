import { mkdirSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { config, DATA_DIR, ROOT, updateSettings } from './config.js';
import { AttachmentBuilder, ChannelType } from 'discord.js';
import { generateImage, generateMusic, listModels, openRouterModels, rankModels } from './llm.js';
import { zip } from './zip.js';
import { rebuildMemories } from './extract.js';
import * as fs from './files.js';
import * as scratch from './scratchpad.js';
import * as git from './git.js';
import { recorderConnected } from './avatarVideo.js';
import * as pressure from './pressure.js';
import { todaysJournal, JOURNAL_DIR } from './journal.js';
import { logFailure } from './failures.js';
import { startTask, listTasks, cancelTask } from './agents.js';
import { MUSIC_DIR, tracks, findTrack, playlists, findPlaylist, savePlaylist, deletePlaylist, addSong } from './music.js';
import { activity, recentActivity } from './events.js';
import { findContact, listContacts, setContactTier, addObligation, openObligations, completeObligation, cancelObligation, personByRealName, reachCard, setReach } from './memory.js';
import { TIERS, isOwner, tierOf } from './contacts.js';

const MAX_FILE_BYTES = 8 * 1024 * 1024; // Discord's upload limit for bots in most servers

// Tools the model can call mid-conversation. Each has an Ollama tool definition and a run(args, ctx) function.
// ctx says who's asking and where (see respond() in companion.js):
//   { perms, client?, guild?, sendHere(attachment)? }. perms comes from contacts.js and gates each tool.
const TOOLS = {
  web_search: {
    enabled: (ctx) => Boolean(config.tavily.apiKey && ctx.perms?.search),
    definition: {
      type: 'function',
      function: {
        name: 'web_search',
        description:
          'Search the internet. Use for current events, news, sports scores, weather, prices, release dates, or any fact you are unsure of or that may have changed recently. Do not use for casual chat.',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'What to search for, phrased like a search engine query' },
            topic: { type: 'string', enum: ['general', 'news'], description: 'Use "news" for recent events and headlines' },
            time_range: { type: 'string', enum: ['day', 'week', 'month', 'year'], description: 'Only include results from this recent period' },
          },
          required: ['query'],
        },
      },
    },
    run: tavilySearch,
  },

  send_file: {
    enabled: (ctx) => Boolean(ctx.sendHere && ctx.perms?.files),
    definition: {
      type: 'function',
      function: {
        name: 'send_file',
        description:
          'Write a file (text, markdown, code, CSV, JSON, HTML, etc.) and send it as a Discord attachment. Use when someone asks you to write, make or send them a file or document. By default it goes to the current chat; set recipient to send it elsewhere.',
        parameters: {
          type: 'object',
          properties: {
            filename: { type: 'string', description: 'File name with extension, e.g. "shopping-list.md" or "notes.txt"' },
            content: { type: 'string', description: 'The complete contents of the file' },
            recipient: {
              type: 'string',
              description:
                'Optional, friends only. A channel name like "#general" to post it in, or the name of a person who has DMed you before to DM it to them. Leave empty to send it right here.',
            },
            message: { type: 'string', description: 'Optional short note to go with the file when sending it elsewhere' },
          },
          required: ['filename', 'content'],
        },
      },
    },
    run: sendFile,
  },

  send_project: {
    enabled: (ctx) => Boolean(ctx.sendHere && ctx.perms?.files),
    definition: {
      type: 'function',
      function: {
        name: 'send_project',
        description:
          'Create a project made of several files (a website, an app, a script with its config, a story in chapters, etc.), bundle it into one .zip and send it as a Discord attachment. Use instead of send_file whenever there is more than one file. Write every file in full, with folders in the paths. By default it goes to the current chat; set recipient to send it elsewhere.',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: 'Project name, used for the .zip, e.g. "todo-app"' },
            files: {
              type: 'array',
              description: 'Every file in the project',
              items: {
                type: 'object',
                properties: {
                  path: { type: 'string', description: 'Path inside the project, e.g. "index.html" or "src/app.js"' },
                  content: { type: 'string', description: 'The complete contents of the file' },
                },
                required: ['path', 'content'],
              },
            },
            recipient: {
              type: 'string',
              description:
                'Optional, friends only. A channel name like "#general" to post it in, or the name of a person who has DMed you before to DM it to them. Leave empty to send it right here.',
            },
            message: { type: 'string', description: 'Optional short note to go with it when sending it elsewhere' },
          },
          required: ['name', 'files'],
        },
      },
    },
    run: sendProject,
  },

  generate_image: {
    enabled: (ctx) => Boolean(config.openrouter.apiKey && (ctx.sendHere || ctx.showImage) && ctx.perms?.files),
    definition: {
      type: 'function',
      function: {
        name: 'generate_image',
        description:
          'Create a picture from a description and show it in this chat. Use when someone asks you to draw, make, generate or show them an image, photo, picture or art.',
        parameters: {
          type: 'object',
          properties: {
            prompt: {
              type: 'string',
              description: 'A detailed description of the image: subject, setting, style, lighting, mood and composition',
            },
          },
          required: ['prompt'],
        },
      },
    },
    run: makeImage,
  },

  generate_music: {
    enabled: (ctx) => Boolean(config.openrouter.apiKey && (ctx.sendHere || ctx.showAudio) && ctx.perms?.files),
    definition: {
      type: 'function',
      function: {
        name: 'generate_music',
        description:
          'Make a song or a piece of music and share it in this chat. Only use once you know what it should be about (topic, mood, genre or style) and how long it should be (a 30-second clip or a full song). If either is missing, ask instead of calling this.',
        parameters: {
          type: 'object',
          properties: {
            prompt: {
              type: 'string',
              description:
                'A detailed description for the music model: genre, mood, tempo, instruments, vocals or instrumental, what the song is about, and any lyrics or themes they asked for',
            },
            length: { type: 'string', enum: ['clip', 'full'], description: '"clip" for a 30-second clip, "full" for a full-length song with verses and chorus' },
            title: { type: 'string', description: 'A short song title, used to find and play it later' },
          },
          required: ['prompt', 'length'],
        },
      },
    },
    run: makeMusic,
  },

  music: {
    enabled: (ctx) => Boolean(ctx.perms?.files && (ctx.voice || ctx.studioMusic || ctx.sendHere)),
    // A getter so the track and playlist lists are current each time the tool is offered.
    get definition() {
      const list = tracks();
      const lists = Object.keys(playlists());
      return {
        type: 'function',
        function: {
          name: 'music',
          description:
            'Play and manage the music library: songs you made plus any your owner added. Play tracks by title, play or save playlists, queue, pause, resume, skip, stop, or say what is playing. ' +
            'To build a playlist from a description ("something chill", "all the rock ones"), pick matching titles from the library below yourself. ' +
            `Library (${list.length}): ${list.map((t) => `"${t.title}"${t.about ? ` (${t.about.slice(0, 80)})` : ''}`).join('; ') || 'empty'}. ` +
            `Playlists: ${lists.map((n) => `"${n}"`).join(', ') || 'none yet'}.`,
          parameters: {
            type: 'object',
            properties: {
              action: {
                type: 'string',
                enum: ['play', 'queue', 'pause', 'resume', 'skip', 'stop', 'now_playing', 'save_playlist', 'delete_playlist', 'list'],
                description: 'play replaces what is playing; queue adds to the end; save_playlist stores tracks under a name without playing',
              },
              tracks: { type: 'array', items: { type: 'string' }, description: 'Track titles from the library, in order' },
              playlist: { type: 'string', description: 'A playlist name: to play, delete, or save the given tracks under' },
              shuffle: { type: 'boolean', description: 'Shuffle the order' },
            },
            required: ['action'],
          },
        },
      };
    },
    run: musicControl,
  },

  // Her working notes. Writing is its own tool with the content fields required: some providers
  // drop every optional argument, which left notes with no text when it was one "action" tool.
  note: {
    enabled: (ctx) => Boolean(ctx.conversation),
    definition: {
      type: 'function',
      function: {
        name: 'note',
        description:
          "Write a note on your private scratchpad for this conversation; you'll see it at the start of every turn. Kinds: mood = how someone is doing and why; thread = a topic in progress; commitment = something you promised to do; open_question = something left unanswered; callback = something to bring back up later. Notes stay private unless you choose to mention them.",
        parameters: {
          type: 'object',
          properties: {
            kind: { type: 'string', enum: scratch.KINDS },
            text: { type: 'string', description: 'One to three short sentences' },
            people: { type: 'array', items: { type: 'string' }, description: 'Names of who it is about' },
            expires_in_hours: { type: 'number', description: 'Optional: drop the note on its own after this many hours' },
          },
          required: ['kind', 'text', 'people'],
        },
      },
    },
    run: writeNote,
  },

  scratchpad: {
    enabled: (ctx) => Boolean(ctx.conversation),
    definition: {
      type: 'function',
      function: {
        name: 'scratchpad',
        description: 'Change a note on your scratchpad: update its text when things change, resolve it once it is settled (it disappears), or list the notes.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['update', 'resolve', 'list'] },
            id: { type: 'number', description: 'The note number (for update or resolve)' },
            text: { type: 'string', description: 'For update: the new text' },
          },
          required: ['action', 'id'],
        },
      },
    },
    run: scratchpadAction,
  },

  thread: {
    enabled: (ctx) => Boolean(ctx.conversation),
    definition: {
      type: 'function',
      function: {
        name: 'thread',
        description:
          `Start or update one of your active threads: at most ${scratch.MAX_THREADS} ongoing situations you follow across every conversation and channel (e.g. "Brandon / Katie living situation"). Using an existing label overwrites its state (current status only, no history). With ${scratch.MAX_THREADS} open, retire one first.`,
        parameters: {
          type: 'object',
          properties: {
            label: { type: 'string', description: 'A short name for the situation' },
            state: { type: 'string', description: 'Its current status, one or two sentences' },
            people: { type: 'array', items: { type: 'string' } },
          },
          required: ['label', 'state', 'people'],
        },
      },
    },
    run: setThreadAction,
  },

  threads: {
    enabled: (ctx) => Boolean(ctx.conversation),
    definition: {
      type: 'function',
      function: {
        name: 'threads',
        description: 'Retire an active thread that is over, or list them.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['retire', 'list'] },
            id: { type: 'number', description: 'For retire: the thread number' },
          },
          required: ['action', 'id'],
        },
      },
    },
    run: threadAction,
  },

  guide: {
    enabled: () => true,
    definition: {
      type: 'function',
      function: {
        name: 'guide',
        description:
          'Your knowledge base about your own tools: what each does, how to fill in its fields, and what to do when it fails. Look up a tool by name (e.g. "files", "git") or a problem ("error", "not available", "truncated", "voice"). Use it when a tool returns an error or you are unsure how to use one, before guessing.',
        parameters: {
          type: 'object',
          properties: {
            topic: { type: 'string', description: 'A tool name or a problem; "" to list the topics' },
          },
          required: ['topic'],
        },
      },
    },
    run: guideLookup,
  },

  checkin_consent: {
    enabled: (ctx) => Boolean(ctx.speaker?.id && ctx.speaker.id !== 'dashboard'),
    definition: {
      type: 'function',
      function: {
        name: 'checkin_consent',
        description:
          "Record whether the person talking to you is okay with you checking in on them on your own now and then (their profile card). Use when they answer your question about it, change their mind, or tell you not to message them. Saves their answer, how often is okay, and what they'd like to talk about or what's too much. A no is permanent: you never ask again.",
        parameters: {
          type: 'object',
          properties: {
            answer: { type: 'string', enum: ['yes', 'no'] },
            every_days: { type: 'number', description: 'For yes: how often is okay, in days (7 = once a week)' },
            note: { type: 'string', description: 'What they want to talk about, tone, or what would be too much' },
            person: { type: 'string', description: 'Owner only: someone else this is for' },
          },
          required: ['answer'],
        },
      },
    },
    run: consentAction,
  },

  obligations: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'obligations',
        description:
          'Owner only. Your to-do list of obligations: things you must do until they are checked off. Add one when your owner asks you to do something later, especially when a person shows up ("when UltraLogic joins, tell him about X", "remind me to ask Chris for the files when he is on"); those fire automatically the next time that person joins voice or talks to you, and are checked off once you have done them. One without a person is a standing priority; check it off with done when it is finished. Also list, or cancel one.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['add', 'list', 'done', 'cancel'] },
            task: { type: 'string', description: 'For add: what you have to do, written so you will understand it later (include what to tell them, and who asked)' },
            person: { type: 'string', description: 'For add: who it is waiting on, by name, if it should happen when they show up' },
            id: { type: 'number', description: 'For done or cancel: the obligation number' },
            note: { type: 'string', description: 'For done: how it was fulfilled' },
          },
          required: ['action'],
        },
      },
    },
    run: obligationAction,
  },

  files: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'files',
        description:
          "Owner only. Work with files and folders on your owner's computer: list a folder, read a file (with line numbers), create a file, make a precise edit, undo the last change, make a folder, " +
          'move or rename a file OR A WHOLE FOLDER, copy a file OR A WHOLE FOLDER (everything inside it, any size; e.g. copy "Desktop/new champ" to "Desktop/new champ backup"), find a file by name, or send a file into this chat. ' +
          'Paths can start with Desktop, Documents, Downloads, Pictures, Music, Videos or ~ (home). You cannot delete anything or run commands. ' +
          'To change a file, read it first, then use edit with old_text copied exactly from it (a few lines, enough to be unique) and the new_text to put in its place. Use write with overwrite only to replace a whole file.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['list', 'read', 'write', 'edit', 'undo', 'mkdir', 'move', 'copy', 'find', 'send'] },
            path: { type: 'string', description: 'The file or folder to act on, always given in full, e.g. "Desktop/my-app" or "Desktop/my-app/src/index.js". For find, the folder to search in.' },
            to: { type: 'string', description: 'Destination for move or copy (a new name, or an existing folder to put it in). Works for whole folders too.' },
            content: { type: 'string', description: 'Full file contents, for write' },
            overwrite: { type: 'boolean', description: 'For write: replace an existing file (its old version is kept for undo)' },
            old_text: { type: 'string', description: 'For edit: the exact existing text to replace' },
            new_text: { type: 'string', description: 'For edit: the text to put in its place' },
            replace_all: { type: 'boolean', description: 'For edit: replace every occurrence' },
            start_line: { type: 'number', description: 'For read: first line to show' },
            end_line: { type: 'number', description: 'For read: last line to show' },
            depth: { type: 'number', description: 'For list: how many folder levels to show (default 1, max 4)' },
            name: { type: 'string', description: 'For find: part of the file name' },
          },
          // path is required: some providers drop optional arguments, and a missing path used to
          // fall back to listing the Desktop, so every "open that folder" showed the Desktop again.
          required: ['action', 'path'],
        },
      },
    },
    run: fileAction,
  },

  self: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'self',
        description:
          'Owner only. Look at yourself as a running program. "map" tells you where everything about you lives: persona file, settings, database (memories, history, notes, obligations), code, logs, and what your current brain and settings are. "journal" shows the journal entries from today. "mood" shows your pressure buckets (boredom, curiosity, irritation, restlessness, usefulness, unease, loneliness per person). "logs" searches your live activity log (the last few hundred events since the bot started): errors, tool calls with timings, what was heard and replied. Use these before stating any cause about how you work.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['map', 'logs', 'mood', 'journal'] },
            type: { type: 'string', description: 'For logs: only this kind of event, e.g. "error", "tool-call", "heard", "replied", "plan"' },
            search: { type: 'string', description: 'For logs: only events containing this text' },
            last: { type: 'number', description: 'For logs: how many of the newest matches (default 30)' },
          },
          required: ['action'],
        },
      },
    },
    run: selfInspect,
  },

  avatar_mode: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'avatar_mode',
        description:
          'Owner only. Turn avatar mode (video replies) on or off: when on, your Discord text replies also get a short video of your face saying them. Or check whether it is on.',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['on', 'off', 'status'] },
          },
          required: ['action'],
        },
      },
    },
    run: avatarMode,
  },

  git: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'git',
        description:
          "Owner only. Git for your owner's projects: status, diff, add (stage files), commit, and push. No other git commands exist. " +
          'Push is two steps: calling push first shows what would be pushed; tell your owner and ask them to confirm. Only after they say yes in their next message, call push again and it goes through. Never say something was pushed unless push returned "Pushed".',
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['status', 'diff', 'add', 'commit', 'push'] },
            repo: { type: 'string', description: 'The project folder (or any path inside it), e.g. "Desktop/lyra-video-game"' },
            files: { type: 'array', items: { type: 'string' }, description: 'For add: files to stage, relative to the project, or ["all"]. For diff: optionally one file.' },
            staged: { type: 'boolean', description: 'For diff: show what is staged instead of unstaged changes' },
            message: { type: 'string', description: 'For commit: the commit message' },
          },
          required: ['action', 'repo'],
        },
      },
    },
    run: gitAction,
  },

  list_models: {
    enabled: (ctx) => Boolean(ctx.perms?.owner && config.openrouter.apiKey),
    definition: {
      type: 'function',
      function: {
        name: 'list_models',
        description:
          'Owner only. Get OpenRouter models as a numbered list, so your owner can pick one by number ("go with 2"). Either the best few for a kind of work (purpose), or a search of the whole catalog by name or maker (search, e.g. "grok", "xai", "claude", "qwen coder"). Present them as 1, 2, 3 with a few words each and the price.',
        parameters: {
          type: 'object',
          properties: {
            purpose: { type: 'string', enum: ['coding', 'reasoning', 'general', 'cheap'], description: 'What the model is for (used when search is empty)' },
            search: { type: 'string', description: 'Words to find in model names or makers, searching every model; "" to rank by purpose instead' },
            count: { type: 'number', description: 'How many to list (default 5 ranked, up to 15 for a search)' },
          },
          // Every field is required: some providers drop optional ones, which silently lost the search.
          required: ['purpose', 'search', 'count'],
        },
      },
    },
    run: showModels,
  },

  background_task: {
    enabled: (ctx) => Boolean(ctx.perms?.owner && config.openrouter.apiKey),
    definition: {
      type: 'function',
      function: {
        name: 'background_task',
        description:
          "Owner only. Hand work to a background helper (a sub-agent) that does it on its own with file access and web search while you keep talking, then reports back here: writing scripts, building or fixing a project, researching something. Also check on running tasks or cancel one. Write the instructions as a complete brief: the goal, which files or folders, and what done looks like.",
        parameters: {
          type: 'object',
          properties: {
            action: { type: 'string', enum: ['start', 'status', 'cancel'] },
            title: { type: 'string', description: 'For start: a short name for the task' },
            instructions: { type: 'string', description: 'For start: the full brief for the helper' },
            model: { type: 'string', description: 'For start: optional OpenRouter model id for the helper; defaults to your current model' },
            id: { type: 'number', description: 'For cancel: the task number' },
          },
          required: ['action'],
        },
      },
    },
    run: backgroundTask,
  },

  switch_brain: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'switch_brain',
        description:
          'Owner only. Change the *teacher* used when HalMini cannot answer (HalMini stays the main brain): "nvidia" (default), "ollama" (local), or "openrouter" (cloud). Can also change the model and, for OpenRouter, reasoning. Use when your owner asks to switch the teacher/provider/model.',
        parameters: {
          type: 'object',
          properties: {
            brain: { type: 'string', enum: ['nvidia', 'ollama', 'openrouter'], description: 'Teacher provider after a HalMini miss' },
            model: { type: 'string', description: 'The model to switch to, as said, e.g. "nemotron", "grok 4.20", "kimi", or an exact id; "" to keep the current one' },
            pick: { type: 'number', description: 'A number from the model list you just showed (list_models), e.g. 2; 0 if not picking from a list' },
            reasoning: { type: 'string', enum: ['none', 'low', 'medium', 'high'], description: 'Optional, OpenRouter only: how hard it thinks before answering' },
          },
          // model and pick are required (empty / 0 when unused): some providers drop optional fields,
          // which once turned "switch to grok 4.20" into a switch that kept the old model.
          required: ['brain', 'model', 'pick'],
        },
      },
    },
    run: switchBrain,
  },

  rebuild_memories: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'rebuild_memories',
        description:
          "Owner only. Read back through the saved conversation history and pull out long-term memories about everyone in it, filed under the right person. Use when your owner asks you to go through the chat history or logs and (re)build, repopulate or refresh your memories. Set fresh only if they also ask to wipe or clear the existing memories first.",
        parameters: {
          type: 'object',
          properties: {
            fresh: { type: 'boolean', description: 'Delete all existing memories first. Only when the owner explicitly asks to clear or wipe them.' },
            this_conversation_only: { type: 'boolean', description: 'Only read the current conversation instead of all of them' },
          },
        },
      },
    },
    run: startRebuild,
  },

  read_source: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    // A getter so the file list is current each time the tool is offered.
    get definition() {
      return {
        type: 'function',
        function: {
          name: 'read_source',
          description:
            'Owner only. Read your own source code: the program that runs you (Discord bot, memory, voice, tools, avatar studio). ' +
            'Use when your owner asks how you work, about your code, or wants you to explain, review or suggest changes to it. ' +
            `Read every file you need in one call. Your files: ${sourceFiles().join(', ')}`,
          parameters: {
            type: 'object',
            properties: {
              paths: { type: 'array', items: { type: 'string' }, description: 'Files to read, relative to the project folder, e.g. ["src/tools.js", "src/llm.js"]' },
              start_line: { type: 'number', description: 'Line to start from, to read the rest of a file that came back TRUNCATED' },
            },
            required: ['paths'],
          },
        },
      };
    },
    run: readSource,
  },

  set_contact_tier: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'set_contact_tier',
        description:
          'Owner only. Move someone on your contact list to a tier: "friend", "guest" or "stranger". Use when your owner tells you to make someone a friend or guest, demote them, etc.',
        parameters: {
          type: 'object',
          properties: {
            person: { type: 'string', description: 'Their name, username or Discord ID' },
            tier: { type: 'string', enum: Object.keys(TIERS) },
          },
          required: ['person', 'tier'],
        },
      },
    },
    run: setTier,
  },

  list_contacts: {
    enabled: (ctx) => Boolean(ctx.perms?.owner),
    definition: {
      type: 'function',
      function: {
        name: 'list_contacts',
        description: "Owner only. See your contact list and everyone's tier. Use when your owner asks who your friends or guests are, or who you know.",
        parameters: {
          type: 'object',
          properties: { tier: { type: 'string', enum: Object.keys(TIERS), description: 'Only list this tier' } },
        },
      },
    },
    run: showContacts,
  },
};

export function enabledTools(ctx = {}) {
  return Object.values(TOOLS)
    .filter((t) => t.enabled(ctx))
    .map((t) => allRequired(t.definition));
}

// Some OpenRouter providers drop every argument that isn't marked required, so a call like
// obligations {action: "add"} arrived without the task (and the files path, the switch_brain
// model, the note text...). So every field is sent as required: one not needed is filled with an
// empty value ("", 0, false, []), which each tool already treats as "not given". Optional enums
// get "" as an allowed value for the same reason.
const EMPTY_HINT = { string: '""', number: '0', boolean: 'false', array: '[]' };
function allRequired(def) {
  const params = def.function.parameters;
  if (!params?.properties) return def;
  const required = new Set(params.required ?? []);
  const properties = {};
  for (const [key, schema] of Object.entries(params.properties)) {
    if (required.has(key)) {
      properties[key] = schema;
      continue;
    }
    const empty = EMPTY_HINT[schema.type] ?? '""';
    properties[key] = {
      ...schema,
      ...(schema.enum ? { enum: ['', ...schema.enum] } : {}),
      description: `${schema.description ? `${schema.description}. ` : ''}Use ${empty} when not needed.`,
    };
  }
  return { ...def, function: { ...def.function, parameters: { ...params, properties, required: Object.keys(properties) } } };
}

// Every call is logged as one line (tool, ok or error, how long, the action) for tuning and
// post-mortems. Arguments and results aren't logged, since they can hold file contents.
export async function runTool(name, args = {}, ctx = {}) {
  const tool = TOOLS[name];
  const started = Date.now();
  const log = (ok, error) =>
    activity('tool-call', { tool: name, ok, error, ms: Date.now() - started, action: parsed?.action, args: parsed && Object.keys(parsed), conversation: ctx.conversation });
  let parsed;
  if (!tool?.enabled(ctx)) {
    log(false, 'not available');
    return structured(name, `Error: ${name} isn't available to you this turn`, ctx, args);
  }
  try {
    parsed = typeof args === 'string' ? JSON.parse(args || '{}') : args ?? {};
    const result = String(await tool.run(parsed, ctx));
    log(!result.startsWith('Error'), result.startsWith('Error') ? result.slice(7, 160) : undefined);
    return structured(name, result, ctx, parsed);
  } catch (err) {
    log(false, err.message);
    activity('error', { where: name, message: err.message });
    return structured(name, `Error: ${err.message}`, ctx, parsed);
  }
}

// Every result starts with one line saying what happened, so success and failure never have to be
// read out of prose: "[files: ok]" then the result, or "[files: error not_found] <what went wrong>"
// then "hint: <the likely fix>". Tools themselves just return text, starting "Error:" on failure.
export const toolFailed = (result) => /^\[[\w-]+: error/.test(String(result));
const ERRORS = [
  ['not_available', /isn't available to you this turn|unknown tool/i, 'This tool was not handed to you this turn. Say so plainly and ask them to repeat the request; don\'t describe a call you didn\'t make.'],
  ['forbidden', /off limits|is private|outside your home|outside the repository/i, "That's never allowed. Don't retry; tell them it's off limits."],
  ['not_allowed', /owner only|only friends|only the owner|can only record|your owner; their access|tier must/i, "This person's access doesn't allow it. Tell them plainly."],
  ['unavailable', /\b429\b|rate limit|timed? ?out|timeout|failed \(5\d\d\)|ECONN|fetch failed|network|unavailable/i, "An outside service is busy or down. Say it didn't work and offer to try again later; don't loop."],
  ['too_large', /too big|over the \d+ ?MB|over discord's|limit is \d+/i, 'Too large to send or return. Offer a smaller version, or another way to get it.'],
  ['conflict', /already exists|appears \d+ times|already \d+ threads|already a |already up to date|nothing to push|nothing is staged|changed since you asked/i, 'Adjust the call: be more specific (more surrounding lines, another name), edit instead of create, or do the missing step first.'],
  ['not_found', /no such|doesn't exist|isn't in |not found|no .{0,40}matches|no note #|no thread #|no open obligation|isn't on your contact|couldn't find|isn't inside a git|nothing to play|no file/i, 'Check the name, number or path; list or find first, or use any suggestion in the message.'],
  ['missing_field', /no path was given|needs|what is the|is empty|empty |missing|say which|isn't a file path|must be one of|needs a name|no content/i, 'A field was empty or wrong. Fill in every field (unused ones as "", 0, false or []) and call again.'],
];
function structured(name, result, ctx = {}, args) {
  if (!result.startsWith('Error')) {
    // A cut-short read isn't a failure, but it's logged so silent short reads can't hide.
    if (/TRUNCATED|NOT READ/.test(result)) logFailure({ conversation: ctx.conversation, kind: 'truncated', tool: name, code: 'truncated', note: result.match(/^.*(TRUNCATED|NOT READ).*$/m)?.[0] });
    return `[${name}: ok]\n${result}`;
  }
  const message = result.replace(/^Error:?\s*/, '');
  const [code, , hint] = ERRORS.find(([, re]) => re.test(message)) ?? ['failed', null, `Read the message, then check guide "${name}" before retrying.`];
  logFailure({ conversation: ctx.conversation, kind: 'hard_fail', tool: name, code, args: args && JSON.stringify(args), note: message });
  return `[${name}: error ${code}] ${message}\nhint: ${hint} (guide topic: "${name}")`;
}

async function tavilySearch({ query, topic = 'general', time_range }) {
  if (!query?.trim()) return 'Error: empty search query';
  const body = {
    query: query.trim(),
    topic: topic === 'news' ? 'news' : 'general',
    search_depth: config.tavily.depth,
    max_results: config.tavily.maxResults,
    include_answer: 'basic',
  };
  if (['day', 'week', 'month', 'year'].includes(time_range)) body.time_range = time_range;

  const started = Date.now();
  const res = await fetch('https://api.tavily.com/search', {
    method: 'POST',
    headers: { Authorization: `Bearer ${config.tavily.apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Tavily search failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  const results = data.results ?? [];
  activity('search', {
    query: body.query,
    results: results.slice(0, 3).map((r) => ({ title: r.title, url: r.url })),
    ms: Date.now() - started,
  });

  if (!results.length && !data.answer) return `No results found for "${body.query}".`;
  return [
    data.answer && `Summary: ${data.answer}`,
    ...results.map(
      (r, i) =>
        `[${i + 1}] ${r.title}${r.published_date ? ` (${r.published_date})` : ''}\n${r.url}\n${(r.content ?? '').slice(0, 600)}`,
    ),
  ]
    .filter(Boolean)
    .join('\n\n');
}

// Draws it with OpenRouter's image model, keeps a copy in data/images, and shows it where the
// conversation is: a Discord attachment, or (in the avatar studio) inline in the chat.
async function makeImage({ prompt }, ctx) {
  if (!prompt?.trim()) return 'Error: empty image prompt';
  const started = Date.now();
  const { mime, bytes } = await generateImage(prompt.trim());
  const ext = mime.split('/')[1].replace('jpeg', 'jpg');
  const name = `${new Date().toISOString().replace(/[:.]/g, '-')}.${ext}`;
  const dir = path.join(DATA_DIR, 'images');
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, name), bytes);
  if (ctx.showImage) await ctx.showImage({ mime, base64: bytes.toString('base64'), name });
  else await ctx.sendHere(new AttachmentBuilder(bytes, { name }));
  activity('file', { filename: name, bytes: bytes.length, to: ctx.showImage ? 'avatar studio' : 'here', ms: Date.now() - started });
  return 'The image was generated and is now showing in the chat. Say something short about it; do not describe it in detail or include links.';
}

async function sendFile({ filename, content, recipient, message }, ctx) {
  if (typeof content !== 'string' || !content.length) return 'Error: the file has no content';
  const name = safeFilename(filename);
  const data = Buffer.from(content, 'utf8');
  if (data.length > MAX_FILE_BYTES) return `Error: ${name} is too big to send (${(data.length / 1048576).toFixed(1)} MB)`;
  // Everything she writes is also kept in data/files.
  const dir = path.join(DATA_DIR, 'files');
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, name), data);
  return deliver(name, data, { recipient, message }, ctx);
}

// Sends an attachment here, to a channel, or by DM, as the asker's tier allows.
async function deliver(name, data, { recipient, message }, ctx) {
  const attachment = () => new AttachmentBuilder(data, { name });
  const note = typeof message === 'string' && message.trim() ? message.trim().slice(0, 2000) : undefined;
  const who = typeof recipient === 'string' ? recipient.trim() : '';

  if (!who || /^(here|this (chat|channel)|me)$/i.test(who)) {
    await ctx.sendHere(attachment());
    activity('file', { filename: name, bytes: data.length, to: 'here' });
    return `Sent ${name} here. It's attached to your reply, so don't paste its contents again.`;
  }

  if (!ctx.perms.sendElsewhere) return 'Error: only friends can have you send files to other channels or people. Tell them you can only send it here.';
  if (!ctx.client) return "Error: the Discord bot is offline, so you can't send it anywhere else right now. Give it to them here instead.";

  const channel = findChannel(ctx, who);
  if (channel) {
    await channel.send({ content: note, files: [attachment()], allowedMentions: { parse: [] } });
    activity('file', { filename: name, bytes: data.length, to: `#${channel.name}` });
    return `Sent ${name} to #${channel.name}.`;
  }

  const contact = findContact(who, { dmOnly: true });
  if (!contact) return `Error: no channel called "${who}" and nobody by that name has DMed you, so you can't send it to them.`;
  const user = await ctx.client.users.fetch(contact.user_id);
  await user.send({ content: note, files: [attachment()], allowedMentions: { parse: [] } });
  activity('file', { filename: name, bytes: data.length, to: contact.name });
  return `Sent ${name} to ${contact.name} by DM.`;
}

// A text channel by name: this server first, then any server the bot is in.
function findChannel(ctx, who) {
  if (!who.startsWith('#') && findContact(who, { dmOnly: true })) return null; // a person's name wins over a channel
  const name = who.replace(/^#/, '').toLowerCase();
  const guilds = [ctx.guild, ...(ctx.client?.guilds.cache.values() ?? [])].filter(Boolean);
  for (const guild of guilds) {
    const channel = guild.channels.cache.find(
      (c) => (c.type === ChannelType.GuildText || c.type === ChannelType.GuildAnnouncement) && c.name.toLowerCase() === name,
    );
    if (channel) return channel;
  }
  return null;
}

// Lyria makes the track; it's kept in data/music and shared like an image.
async function makeMusic({ prompt, length, title }, ctx) {
  if (!prompt?.trim()) return 'Error: empty music prompt';
  const full = length === 'full';
  const started = Date.now();
  const { bytes, lyrics } = await generateMusic(prompt.trim(), full ? 'full' : 'clip');
  // Into the library (data/music) under its title, so it can be played and playlisted later.
  const name = addSong(bytes, {
    title: (typeof title === 'string' && title.trim()) || prompt.split(/[,.;:]/)[0].trim().split(/\s+/).slice(0, 6).join(' '),
    about: prompt.trim().slice(0, 300),
  });
  if (bytes.length > MAX_FILE_BYTES && !ctx.showAudio) return `The song is saved in the music library, but it's too big to attach on Discord (${(bytes.length / 1048576).toFixed(1)} MB). Offer to play it in voice instead.`;
  if (ctx.showAudio) await ctx.showAudio({ mime: 'audio/mpeg', base64: bytes.toString('base64'), name });
  else await ctx.sendHere(new AttachmentBuilder(bytes, { name }));
  activity('file', { filename: name, bytes: bytes.length, to: ctx.showAudio ? 'avatar studio' : 'here', ms: Date.now() - started });
  const words = lyrics && lyrics !== '<instrumental>' ? ` Its lyrics: ${lyrics.slice(0, 1500)}` : ' It is instrumental.';
  return `The ${full ? 'song' : '30-second clip'} is done and now playing in the chat.${words} Say something short about it; don't read out all the lyrics.`;
}

// Changes the reply brain from conversation. A model name can be partial ("kimi") and is matched
// against what's actually available, so a typo can't leave her without a brain.
function normalizeBrain(v) {
  const b = String(v || '').toLowerCase().trim();
  if (b === 'openrouter') return 'openrouter';
  if (b === 'nvidia' || b === 'nim') return 'nvidia';
  return 'ollama';
}

function brainLabel(brain, { openrouterModel, ollamaModel, nvidiaModel, openrouterReasoning } = {}) {
  if (brain === 'openrouter') return `teacher OpenRouter, model ${openrouterModel ?? config.openrouter.model}, reasoning ${openrouterReasoning ?? config.openrouter.reasoning}`;
  if (brain === 'nvidia') return `teacher NVIDIA NIM, model ${nvidiaModel ?? config.nvidia.model}`;
  return `teacher local Ollama, model ${ollamaModel ?? config.ollama.model}`;
}

function brainShort(brain, models = {}) {
  if (brain === 'openrouter') return `OpenRouter ${models.openrouterModel ?? config.openrouter.model}`;
  if (brain === 'nvidia') return `NVIDIA ${models.nvidiaModel ?? config.nvidia.model}`;
  return `local Ollama ${models.ollamaModel ?? config.ollama.model}`;
}

async function switchBrain({ brain, model, reasoning, pick }, ctx = {}) {
  // "Go with 2": the number refers to the last list shown in this conversation.
  if (Number(pick) > 0) {
    const shown = lastModelList.get(ctx.conversation) ?? [];
    const chosen = shown[Number(pick) - 1];
    if (!chosen) return `Error: there's no number ${pick} in the last model list${shown.length ? ` (it had ${shown.length})` : '; show one with list_models first'}.`;
    brain = 'openrouter';
    model = chosen;
  }
  const patch = { brain: normalizeBrain(brain) };
  if (patch.brain === 'openrouter' && !config.openrouter.apiKey) return "Error: there's no OpenRouter key in .env, so you can't switch to it.";
  if (patch.brain === 'nvidia' && !config.nvidia.apiKey) return "Error: there's no NVIDIA_API_KEY in .env, so you can't switch to NVIDIA. Stay on the current brain.";
  const want = typeof model === 'string' ? model.trim() : '';
  let alsoMatched = [];
  if (want) {
    if (patch.brain === 'nvidia') {
      // NIM has no public catalogue here — accept the id/partial as given.
      const known = [
        config.nvidia.model,
        'nvidia/nemotron-3.5-super-vl-120b-a12b',
        'nvidia/llama-3.3-nemotron-super-49b-v1.5',
      ];
      const hit = known.find((id) => id.toLowerCase().includes(want.toLowerCase())) || want;
      alsoMatched = [hit];
      patch.nvidiaModel = hit;
    } else {
      const options = patch.brain === 'openrouter'
        ? (await openRouterModels()).filter((m) => m.tools && !m.image).map((m) => ({ id: m.id, name: m.name }))
        : (await listModels()).map((m) => ({ id: m.name, name: m.name }));
      const matches = matchModels(options, want);
      if (!matches.length) {
        const near = closestModels(options, want);
        return `Error: no ${patch.brain} model matches "${model}", so nothing was switched. ${near.length ? `Closest real ids: ${near.join(', ')}. Ask which one they mean.` : 'Ask which one they mean, or search with list_models.'}`;
      }
      alsoMatched = matches;
      patch[patch.brain === 'openrouter' ? 'openrouterModel' : 'ollamaModel'] = matches[0];
    }
  }
  const before = brainShort(config.brain);
  if (reasoning && ['none', 'low', 'medium', 'high'].includes(reasoning)) patch.openrouterReasoning = reasoning;
  const s = updateSettings(patch);
  const now = brainLabel(s.brain, s);
  const after = brainShort(s.brain, s);
  if (after === before && !patch.openrouterReasoning) return `Nothing changed: you're still on ${now}. If they named a model, it didn't come through; ask them to say it again.`;
  activity('persona', { reason: `Brain switched to ${now}`, changedBy: 'owner' });
  const currentId = s.brain === 'openrouter' ? s.openrouterModel : s.brain === 'nvidia' ? s.nvidiaModel : s.ollamaModel;
  const others = want ? alsoMatched.filter((id) => id !== currentId).slice(0, 4) : [];
  return `Switched teacher. HalMini is still the main brain; on a miss you now use ${now}. Tell them briefly.` +
    (others.length ? ` "${model}" also matched ${others.join(', ')}; mention you picked this one and they can name another.` : '');
}

// Plays music where the conversation is: the Discord voice channel she's in, the avatar studio's
// page, or (on Discord with no voice session) as attachments in the chat.
async function musicControl({ action, tracks: titles, playlist, shuffle }, ctx) {
  const library = tracks();
  const player = ctx.voice ?? null;
  const studio = ctx.studioMusic ?? null;
  const title = (file) => library.find((t) => t.file === file)?.title ?? file;

  // Resolve titles and/or a playlist into files.
  let files = [];
  const missing = [];
  for (const t of Array.isArray(titles) ? titles : []) {
    const found = findTrack(t, library);
    if (found) files.push(found.file);
    else missing.push(t);
  }
  let named = null;
  if (playlist && !['save_playlist', 'delete_playlist'].includes(action)) {
    named = findPlaylist(playlist);
    if (!named) return `Error: no playlist called "${playlist}". Playlists: ${Object.keys(playlists()).join(', ') || 'none yet'}.`;
    files.push(...named.files);
  }
  files = [...new Set(files)];
  if (shuffle) files.sort(() => Math.random() - 0.5);
  const missed = missing.length ? ` Couldn't find: ${missing.join(', ')}.` : '';

  switch (action) {
    case 'list':
      return `Library: ${library.map((t) => t.title).join('; ') || 'empty'}. Playlists: ${Object.entries(playlists()).map(([n, f]) => `${n} (${f.length})`).join(', ') || 'none'}.`;
    case 'save_playlist': {
      if (!playlist?.trim()) return 'Error: a playlist needs a name.';
      if (!files.length) return `Error: none of those tracks are in the library.${missed}`;
      const name = savePlaylist(playlist.trim(), files);
      return `Saved playlist "${name}" with ${files.length} tracks: ${files.map(title).join(', ')}.${missed}`;
    }
    case 'delete_playlist': {
      const gone = deletePlaylist(playlist ?? '');
      return gone ? `Deleted playlist "${gone}".` : `Error: no playlist called "${playlist}".`;
    }
    case 'play':
    case 'queue': {
      if (!files.length) return `Error: nothing to play.${missed} Library: ${library.map((t) => t.title).join('; ') || 'empty'}.`;
      const append = action === 'queue';
      if (player) player.playMusic(files, { append });
      else if (studio) studio({ action: append ? 'queue' : 'play', tracks: files.map((f) => ({ title: title(f), url: `/api/music/${encodeURIComponent(f)}` })) });
      else if (ctx.sendHere) {
        // Not in voice: hand over the files instead (Discord allows 10 per message).
        for (const f of files.slice(0, 10)) await ctx.sendHere(new AttachmentBuilder(readFileSync(path.join(MUSIC_DIR, f)), { name: f }));
        return `You're not in a voice channel, so the songs are attached here instead: ${files.slice(0, 10).map(title).join(', ')}.${missed} Mention /join to play them out loud.`;
      }
      activity('music', { action, tracks: files });
      return `${append ? 'Queued' : 'Now playing'}${named ? ` playlist "${named.name}"` : ''}: ${files.map(title).join(', ')}.${missed} Keep your reply short; the music is playing.`;
    }
    case 'pause':
    case 'resume':
    case 'skip':
    case 'stop': {
      if (studio) {
        studio({ action });
        return `Done: ${action}.`;
      }
      if (!player) return "Error: nothing is playing; you're not in a voice channel.";
      const ok = { pause: () => player.pauseMusic(), resume: () => player.resumeMusic(), skip: () => player.skipMusic(), stop: () => (player.stopMusic(), true) }[action]();
      if (action === 'skip' && player.music.on) return `Skipped. Now playing: ${title(player.musicStatus().current)}.`;
      return ok ? `Done: ${action}.` : 'Nothing is playing right now.';
    }
    case 'now_playing': {
      if (!player) return studio ? 'The song title is shown in the player on the page.' : 'Nothing is playing.';
      const s = player.musicStatus();
      return s.on ? `Playing${s.paused ? ' (paused)' : ''}: ${title(s.current)}. Up next: ${s.upNext.map(title).join(', ') || 'nothing'}.` : 'Nothing is playing.';
    }
    default:
      return `Error: unknown action "${action}"`;
  }
}

async function writeNote({ kind, text, people, expires_in_hours }, ctx) {
  if (!scratch.KINDS.includes(kind) || !text?.trim()) {
    return `Error: a note needs kind (${scratch.KINDS.join(', ')}) and text. Example: {"kind": "commitment", "text": "Check in with Sam on Thursday about the report redo.", "people": ["Sam"]}`;
  }
  return `Noted as #${scratch.write(ctx.conversation, { kind, text, people, expiresInHours: expires_in_hours })}.`;
}

async function scratchpadAction({ action, id, text }, ctx) {
  const c = ctx.conversation;
  if (action === 'list') {
    const all = scratch.entries(c);
    return all.length ? all.map((e) => `#${e.id} [${e.kind}] ${e.text}`).join('\n') : 'The scratchpad is empty.';
  }
  if (action === 'update') return scratch.update(c, Number(id), { text }) ? `Updated #${id}.` : `Error: no note #${id} in this conversation.`;
  if (action === 'resolve') return scratch.resolve(c, Number(id)) ? `Resolved #${id}.` : `Error: no note #${id} in this conversation (already resolved?).`;
  return `Error: unknown action "${action}"`;
}

async function setThreadAction({ label, state, people }) {
  if (!label?.trim() || !state?.trim()) return 'Error: a thread needs label and state. Example: {"label": "Sam\'s report redo", "state": "Boss rejected it; redo due Friday.", "people": ["Sam"]}';
  return `Thread #${scratch.setThread({ label, state, people })} is up to date.`;
}

async function threadAction({ action, id }) {
  if (action === 'list') {
    const all = scratch.threads();
    return all.length ? all.map((t) => `#${t.id} ${t.label}: ${t.state}`).join('\n') : 'No active threads.';
  }
  if (action === 'retire') return scratch.retireThread(Number(id)) ? `Retired thread #${id}.` : `Error: no thread #${id}.`;
  return `Error: unknown action "${action}"`;
}

// The knowledge base (knowledge/tools.md), one "## topic" section per tool or problem. A topic
// matches a section name first ("thread" finds "thread / threads"), else the sections that mention it.
const GUIDE_FILE = path.join(ROOT, 'knowledge', 'tools.md');
async function guideLookup({ topic }) {
  let text;
  try {
    text = readFileSync(GUIDE_FILE, 'utf8');
  } catch {
    return `Error: the knowledge base file is missing (${GUIDE_FILE}).`;
  }
  const sections = text.split(/^## /m).slice(1).map((s) => {
    const [head, ...body] = s.split('\n');
    return { names: head.split('/').map((n) => n.trim().toLowerCase()), title: head.trim(), body: body.join('\n').trim() };
  });
  const want = String(topic ?? '').trim().toLowerCase();
  if (!want) return `Topics: ${sections.map((s) => s.title).join(', ')}. Ask for one by name.`;
  const named = sections.filter((s) => s.names.some((n) => n === want || n.replace(/[_ -]/g, '') === want.replace(/[_ -]/g, '')));
  const hits = named.length ? named : sections.filter((s) => `${s.title}\n${s.body}`.toLowerCase().includes(want)).slice(0, 3);
  if (!hits.length) return `Nothing about "${topic}". Topics: ${sections.map((s) => s.title).join(', ')}.`;
  return hits.map((s) => `## ${s.title}\n${s.body}`).join('\n\n');
}

// Their own answer goes on their own card. Only the owner can set someone else's.
async function consentAction({ answer, every_days, note, person }, ctx) {
  let who = ctx.speaker;
  const self = typeof person === 'string' && [ctx.speaker?.name, ctx.speaker?.username].some((n) => n && n.toLowerCase() === person.trim().toLowerCase());
  if (typeof person === 'string' && person.trim() && !self) {
    if (!ctx.perms?.owner) return "Error: you can only record the person you're talking to (leave person empty for them).";
    const c = findContact(person.trim()) ?? listContacts().find((x) => x.user_id === personByRealName(person.trim()));
    if (!c) return `Error: "${person}" isn't on your contact list.`;
    who = { id: c.user_id, name: c.name };
  }
  if (!['yes', 'no'].includes(answer)) return 'Error: answer must be yes or no.';
  const card = reachCard(who.id);
  if (!card) return `Error: ${who.name} isn't on your contact list yet.`;
  if (card.reach_out === 'no' && answer === 'yes') return `${who.name} said no before, and a no is permanent, so nothing changed. Don't ask again.`;
  setReach(who.id, { answer, everyDays: Number(every_days) || 7, note: typeof note === 'string' && note.trim() ? note.trim() : null });
  activity('checkin', { name: who.name, answer, every_days: answer === 'yes' ? Number(every_days) || 7 : null });
  return answer === 'yes'
    ? `Saved on ${who.name}'s card: yes, at most every ${Number(every_days) || 7} day(s)${note ? `; note: ${note}` : ''}. You'll check in on your own within that, only while you're in a voice call with them for now.`
    : `Saved on ${who.name}'s card: no. You won't reach out to them on your own, and won't ask again.`;
}

async function obligationAction({ action, task, person, id, note }, ctx) {
  if (action === 'list') {
    const open = openObligations();
    return open.length
      ? open.map((o) => `#${o.id} ${o.target_name ? `[when ${o.target_name} shows up] ` : '[priority] '}${o.task}`).join('\n')
      : 'No open obligations.';
  }
  if (action === 'done') return completeObligation(Number(id), note || null) ? `Checked off #${id}.` : `Error: no open obligation #${id}.`;
  if (action === 'cancel') return cancelObligation(Number(id)) ? `Cancelled #${id}.` : `Error: no open obligation #${id}.`;
  if (!task?.trim()) return 'Error: what is the obligation?';
  const who = typeof person === 'string' && person.trim() ? person.trim() : null;
  // Someone she knows is matched by id, including by a real name or nickname she remembers ("Brandon"
  // is ILGAV3NG3R); a name she doesn't know at all waits for someone with that name to show up.
  let contact = who ? findContact(who) : null;
  if (who && !contact) {
    const id = personByRealName(who);
    contact = id ? listContacts().find((c) => c.user_id === id) ?? null : null;
  }
  const newId = addObligation({ task: task.trim(), targetId: contact?.user_id ?? null, targetName: contact?.name ?? who, createdBy: ctx.perms?.owner ? 'owner' : null });
  const what = task.trim().replace(/\.+$/, '');
  return who
    ? `Added obligation #${newId}, for when ${contact?.name ?? who} next joins voice or talks to you: ${what}.${contact ? '' : ` You haven't met anyone called ${who} yet, so it'll match whoever shows up with that name.`}`
    : `Added obligation #${newId} to your priorities: ${what}. Check it off when it's done.`;
}

async function fileAction(args, ctx) {
  const { action, path: p, to } = args;
  if (!p?.trim() && action !== 'find') {
    return `Error: no path was given, so nothing was ${action === 'list' ? 'listed' : 'done'}. Call files again with the full path, e.g. {"action": "${action}", "path": "Desktop/lyra-video-game"}.`;
  }
  switch (action) {
    case 'list': return fs.list(p, Math.min(4, Math.max(1, Number(args.depth) || 1)));
    case 'read': return fs.read(p, args.start_line, args.end_line);
    case 'write': return fs.write(p, args.content, args.overwrite === true);
    case 'edit': return fs.edit(p, args.old_text, args.new_text, args.replace_all === true);
    case 'undo': return fs.undo(p);
    case 'mkdir': return fs.makeFolder(p);
    case 'move': return fs.move(p, to);
    case 'copy': return fs.copy(p, to);
    case 'find': return fs.find(args.name, p || 'home');
    case 'send': {
      const { name, bytes } = fs.fileBytes(p);
      if (bytes.length > MAX_FILE_BYTES) return `Error: ${name} is ${(bytes.length / 1048576).toFixed(1)} MB, over Discord's 8 MB limit.`;
      if (!ctx.sendHere) return 'Error: there is no chat here to send a file into.';
      await ctx.sendHere(new AttachmentBuilder(bytes, { name }));
      return `Sent ${name} here.`;
    }
    default: return `Error: unknown action "${action}"`;
  }
}

// Her own layout, so "where is my persona / log / memory" is a lookup, not a search.
async function selfInspect({ action, type, search, last }) {
  if (action === 'journal') return todaysJournal() || 'Nothing in your journal today yet.';
  if (action === 'mood') {
    const p = pressure.snapshot();
    const lone = Object.values(p.loneliness).filter((x) => x.value > 0).map((x) => `${x.name} ${x.value.toFixed(2)}`).join(', ') || 'nobody';
    return `Pressure buckets (0-1): ${Object.entries(p.buckets).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(', ')}; loneliness: ${lone}. Total ${p.total} (cap ${p.cap}). Last want: ${p.last_action ? `${p.last_action.bucket}${p.last_action.person ? ` (${p.last_action.person})` : ''} at ${new Date(p.last_action.at).toLocaleTimeString()}` : 'none yet'}. Actions are ${p.actions_live ? 'live' : 'logged only'}; tuning is in ${p.config}. Fired wants are in the log under type "pressure".`;
  }
  if (action === 'logs') {
    const want = String(search ?? '').toLowerCase();
    const hits = recentActivity.filter((e) => (!type || e.type === type) && (!want || JSON.stringify(e).toLowerCase().includes(want)));
    const n = Math.min(100, Math.max(1, Number(last) || 30));
    if (!hits.length) return `No matching events among the last ${recentActivity.length} (the log starts when the bot starts and keeps the newest 300).`;
    return hits.slice(-n).map((e) => {
      const { type: t, time, ...rest } = e;
      return `${new Date(time).toLocaleTimeString()} ${t} ${JSON.stringify(rest).slice(0, 300)}`;
    }).join('\n');
  }
  return [
    `Your runtime (all paths on this PC; the project is ${ROOT}):`,
    `- Persona (your personality and voice): ${path.join(ROOT, 'persona.md')}. It changes when your owner asks you to change how you act; you can read it with read_source (persona.md).`,
    `- Your built-in instructions (abilities, rules, how replies work): src/capabilities.js and src/companion.js, readable with read_source. They're code, so changes to them are made by your owner (or Claude), not by you.`,
    `- Settings: .env (keys; private, never readable) and ${path.join(DATA_DIR, 'settings.json')} (dashboard and your own switches).`,
    `- Database: ${path.join(DATA_DIR, 'companion.db')}: messages, long-term memories, summaries, contacts and tiers, obligations, scratchpad notes, threads. Private; reach it through your tools.`,
    `- Files: music ${path.join(DATA_DIR, 'music')}, playlists ${path.join(DATA_DIR, 'playlists.json')}, images ${path.join(DATA_DIR, 'images')}, written files ${path.join(DATA_DIR, 'files')}, background task reports ${path.join(DATA_DIR, 'tasks')}, file edit history ${path.join(DATA_DIR, 'file-history')}.`,
    `- Journal: ${JOURNAL_DIR} (one file per day; read today's with self "journal").`,
    `- Logs: there is no log file. Your activity log lives in memory (the dashboard's live feed shows it); search it with self (action "logs"). It holds the newest 300 events since the bot started.`,
    `- Right now: main brain HalMini (${config.halmini.enabled ? 'on' : 'off'}); ${brainLabel(config.brain)}; speech-to-text ${config.stt}; avatar mode ${config.avatarVideo ? 'on' : 'off'}; voice wake word "${config.voice.wakeWord || '(none)'}".`,
  ].join('\n');
}

// Saved like the dashboard settings, so it stays as set across restarts.
async function avatarMode({ action }) {
  if (action === 'on' || action === 'off') {
    updateSettings({ avatarVideo: action === 'on' });
    activity('settings', { message: `Avatar mode (video replies) turned ${action}` });
  }
  const on = config.avatarVideo;
  const page = recorderConnected();
  if (!on) return `Avatar mode is off: replies are text only.`;
  return page
    ? `Avatar mode is on: Discord text replies also get a video of you saying them.`
    : `Avatar mode is on, but the Recorder page isn't open, so no videos will be made until your owner opens it from the dashboard (Recorder button), clicks it once, and leaves it open.`;
}

// Pushes wait for the owner: the first push call only previews and remembers it here. It goes
// through on a later turn, within 10 minutes, if the owner's own message says yes and nothing
// new was committed in between. The model can't confirm for them.
const pendingPushes = new Map(); // conversation -> { plan, turn, at }
const YES = /\b(yes|yeah|yep|yup|sure|confirm(ed)?|go ahead|do it|push it|send it|ship it|ok(ay)?)\b/i;
const NO = /\b(no|nope|don'?t|do not|wait|hold on|hold off|cancel|stop|not yet)\b/i;
async function gitAction({ action, repo, files, staged, message }, ctx) {
  const dir = await git.repoOf(repo);
  if (action === 'status') return git.status(dir);
  if (action === 'diff') return git.diff(dir, { file: Array.isArray(files) ? files[0] : files, staged });
  if (action === 'add') {
    if (!files || (Array.isArray(files) && !files.length)) return 'Error: say which files to add, or ["all"].';
    return git.add(dir, files);
  }
  if (action === 'commit') return git.commit(dir, message);
  if (action !== 'push') return `Error: unknown action "${action}"`;

  const pending = pendingPushes.get(ctx.conversation);
  const confirmed = pending && pending.turn !== ctx.turn && Date.now() - pending.at < 10 * 60_000 && YES.test(ctx.userText ?? '') && !NO.test(ctx.userText ?? '');
  if (confirmed) {
    pendingPushes.delete(ctx.conversation);
    const now = await git.preparePush(dir);
    if (now.repo !== pending.plan.repo || now.branch !== pending.plan.branch || now.commits !== pending.plan.commits) {
      pendingPushes.set(ctx.conversation, { plan: now, turn: ctx.turn, at: Date.now() });
      return `Error: what would be pushed changed since you asked. New preview: ${preview(now)} Ask them to confirm again.`;
    }
    const result = await git.push(now);
    activity('git', { push: `${now.branch} -> ${now.remote}`, repo: dir });
    return result;
  }
  const plan = await git.preparePush(dir);
  pendingPushes.set(ctx.conversation, { plan, turn: ctx.turn, at: Date.now() });
  return `Not pushed yet. ${preview(plan)} Tell your owner exactly this and ask them to confirm; push only goes through after they say yes.`;
}
const preview = (p) =>
  `Ready to push branch ${p.branch} to ${p.remote} (${p.url})${p.upstream ? '' : ', creating it there for the first time'}. Commits:\n${p.commits || '(none)'}\n`;

// Model names as people say them ("grok 4.20", "Claude Opus") against ids like "x-ai/grok-4.20":
// compared without spaces or punctuation, over the id and the display name. Best first: an exact
// id, then the id ending with it, then the name, then anything containing it; shorter wins ties,
// so "kimi" picks the plain model over ":free" or ":batch" variants.
const squash = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
function matchModels(options, said) {
  const w = squash(said);
  if (!w) return [];
  const score = ({ id, name }) => {
    const i = squash(id), tail = squash(id.split('/').pop()), n = squash(name);
    if (i === w || tail === w) return 0;
    if (tail.startsWith(w) || n.endsWith(w)) return 1;
    if (n.includes(w)) return 2;
    if (i.includes(w)) return 3;
    return -1;
  };
  return options
    .map((o) => ({ ...o, s: score(o) }))
    .filter((o) => o.s >= 0 && !o.id.startsWith('~'))
    .sort((a, b) => a.s - b.s || a.id.length - b.id.length)
    .map((o) => o.id);
}
// When nothing matches: ids sharing the most words with what was said.
function closestModels(options, said) {
  const words = String(said).toLowerCase().split(/[^a-z0-9.]+/).filter((x) => x.length > 1);
  return options
    .map((o) => ({ id: o.id, hits: words.filter((x) => `${o.id} ${o.name}`.toLowerCase().includes(x)).length }))
    .filter((o) => o.hits)
    .sort((a, b) => b.hits - a.hits || a.id.length - b.id.length)
    .slice(0, 6)
    .map((o) => o.id);
}

// The last numbered model list shown in each conversation, so "go with 2" means something.
const lastModelList = new Map();
async function showModels({ purpose = 'coding', count, search }, ctx) {
  let picks, title;
  if (typeof search === 'string' && search.trim()) {
    // The whole catalog, not the ranked slice: every word said must appear in the id or name.
    const words = search.toLowerCase().split(/[^a-z0-9.]+/).filter(Boolean);
    const all = (await openRouterModels()).filter((m) => m.tools && !m.image && !m.id.startsWith('~'));
    picks = all
      .filter((m) => words.every((w) => squash(`${m.id} ${m.name}`).includes(squash(w))))
      .sort((a, b) => b.created - a.created)
      .slice(0, Math.min(15, Math.max(1, Number(count) || 10)));
    if (!picks.length) return `No OpenRouter model (that can use tools) matches "${search}". ${closestModels(all, search).length ? `Closest: ${closestModels(all, search).join(', ')}.` : ''}`;
    title = `OpenRouter models matching "${search}", newest first`;
  } else {
    picks = await rankModels(purpose, Math.min(8, Math.max(1, Number(count) || 5)));
    if (!picks.length) return 'Error: no models came back from OpenRouter.';
    title = `Best ${purpose} models on OpenRouter right now`;
  }
  lastModelList.set(ctx.conversation, picks.map((m) => m.id));
  const current = config.brain === 'openrouter' ? config.openrouter.model : null;
  return (
    `${title} (price per million tokens in/out):\n` +
    picks.map((m, i) => `${i + 1}. ${m.name} (${m.id}) - $${m.price.toFixed(2)}/$${m.outPrice.toFixed(2)}, ${Math.round(m.context / 1000)}k context${m.reasoning ? ', reasoning' : ''}${m.id === current ? ' [current]' : ''}`).join('\n') +
    `\nPresent these as a numbered list and ask which number they want. When they pick one, call switch_brain with pick set to that number.`
  );
}

// Helpers get the tools that make sense unattended: files, web search, reading her own code.
const HELPER_TOOLS = ['files', 'web_search', 'read_source'];
async function backgroundTask({ action, title, instructions, model, id }, ctx) {
  if (action === 'status') {
    const all = listTasks();
    return all.length ? JSON.stringify(all) : 'No background tasks yet.';
  }
  if (action === 'cancel') return cancelTask(id) ? `Cancelling task #${id}.` : `Error: no running task #${id}.`;
  if (!instructions?.trim()) return 'Error: the helper needs instructions.';
  const helperCtx = { perms: ctx.perms, conversation: ctx.conversation };
  const tools = HELPER_TOOLS.map((n) => TOOLS[n]).filter((t) => t.enabled(helperCtx)).map((t) => t.definition);
  const task = startTask({
    title,
    instructions,
    model: typeof model === 'string' && model.trim() ? model.trim() : config.openrouter.model,
    tools,
    run: (name, args) => (HELPER_TOOLS.includes(name) ? runTool(name, args, helperCtx) : `Error: helpers can't use ${name}`),
    notify: ctx.notify,
  });
  return `Started background task #${task.id} "${task.title}" on ${task.model}. It works on its own and ${ctx.notify ? 'will report back here when done' : 'you can check on it by asking for its status'}. Tell them briefly.`;
}

// Kicks off the history read in the background; she answers straight away.
async function startRebuild({ fresh, this_conversation_only }, ctx) {
  const run = rebuildMemories({ fresh: fresh === true, only: this_conversation_only ? ctx.conversation : null });
  if (!run.started) return `Error: ${run.message}`;
  return `Started reading ${run.conversations} conversation${run.conversations === 1 ? '' : 's'} to rebuild your memories${fresh ? ', after clearing the old ones' : ''}. It runs in the background and takes a few minutes; the dashboard's activity feed shows when it's done. Strangers aren't remembered, only guests, friends and the owner. Tell them briefly.`;
}

// Her own code, read-only. Secrets and private data are never readable: .env (keys and tokens),
// data/ (database, memories, settings), dependencies and git internals.
const SOURCE_BLOCKED = /^(\.env|data|models|node_modules|\.git|\.claude|package-lock\.json)(\/|$)|(^|\/)\.env(\.|$)|\.(db|db-wal|db-shm|glb|png|jpe?g|zip)$/i;
const MAX_SOURCE_CHARS = 60_000; // per file
const MAX_SOURCE_CALL_CHARS = 160_000; // per call, across all its files
function sourceFiles(dir = ROOT, rel = '') {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = rel ? `${rel}/${entry.name}` : entry.name;
    if (SOURCE_BLOCKED.test(p)) continue;
    if (entry.isDirectory()) out.push(...sourceFiles(path.join(dir, entry.name), p));
    else out.push(p);
  }
  return out;
}
// Each file gets its own budget (one big file used to eat the whole call and leave the rest as
// "first 0 of N characters", which read like a successful read). A file cut short says so loudly,
// with the line to continue from; files past the call's overall limit are named as not read.
async function readSource({ paths, path: one, start_line } = {}) {
  const files = sourceFiles();
  const wanted = (Array.isArray(paths) ? paths : [paths ?? one]).filter((p) => typeof p === 'string' && p.trim()).slice(0, 8);
  if (!wanted.length) return `Your source files:\n${files.join('\n')}`;
  const from = Math.max(1, Number(start_line) || 1);
  let callLeft = MAX_SOURCE_CALL_CHARS;
  const parts = [];
  for (const want of wanted) {
    const rel = want.replace(/\\/g, '/').replace(/^\.?\//, '').trim();
    if (rel.split('/').includes('..') || SOURCE_BLOCKED.test(rel)) {
      parts.push(`${rel}: private, can't be read.`);
      continue;
    }
    if (!files.includes(rel)) {
      const near = files.filter((f) => f.endsWith(`/${rel}`) || f.includes(rel)).slice(0, 5);
      parts.push(`${rel}: no such file.${near.length ? ` Did you mean: ${near.join(', ')}?` : ''}`);
      continue;
    }
    if (callLeft < 2000) {
      parts.push(`${rel}: NOT READ, this call's limit was used up by the files before it. Read it in a separate call.`);
      continue;
    }
    const source = readFileSync(path.join(ROOT, rel), 'utf8');
    const lines = source.split(/\r?\n/);
    const total = source.length;
    const cap = Math.min(MAX_SOURCE_CHARS, callLeft);
    let out = '', n = from;
    for (; n <= lines.length; n++) {
      const line = `${n}| ${lines[n - 1]}\n`;
      if (out.length + line.length > cap) break;
      out += line;
    }
    callLeft -= out.length;
    const last = n - 1;
    parts.push(
      last < lines.length
        ? `${rel} TRUNCATED: showing lines ${from}-${last} of ${lines.length} (${out.length} of ~${total} characters). Read the rest with paths ["${rel}"] and start_line ${last + 1}.\n${out}`
        : `${rel} (lines ${from}-${lines.length} of ${lines.length}, complete, ${out.length} characters):\n${out}`,
    );
  }
  return parts.join('\n\n----\n\n');
}

// Several files zipped into one attachment. The unpacked project is kept in data/projects/<name>.
const MAX_PROJECT_FILES = 200;
async function sendProject({ name, files, recipient, message }, ctx) {
  if (!Array.isArray(files) || !files.length) return 'Error: the project has no files';
  if (files.length > MAX_PROJECT_FILES) return `Error: too many files (${files.length}); the limit is ${MAX_PROJECT_FILES}`;
  const project = safeFilename(name).replace(/\.[a-z0-9]+$/i, '') || 'project';
  const entries = new Map();
  for (const f of files) {
    if (typeof f?.content !== 'string') continue;
    const rel = safePath(f.path);
    if (rel) entries.set(rel, Buffer.from(f.content, 'utf8')); // a repeated path: the last one wins
  }
  if (!entries.size) return 'Error: none of the files had a usable path and content';
  const list = [...entries].map(([p, data]) => ({ path: `${project}/${p}`, data }));
  const archive = zip(list);
  if (archive.length > MAX_FILE_BYTES) return `Error: the project is too big to send (${(archive.length / 1048576).toFixed(1)} MB)`;

  const dir = path.join(DATA_DIR, 'projects', project);
  for (const [p, data] of entries) {
    mkdirSync(path.dirname(path.join(dir, p)), { recursive: true });
    writeFileSync(path.join(dir, p), data);
  }
  const result = await deliver(`${project}.zip`, archive, { recipient, message }, ctx);
  return result.startsWith('Error') ? result : `${result} (${entries.size} files: ${[...entries.keys()].join(', ')})`;
}

// A relative path inside a project: forward slashes, no "..", no drive letters or leading slash.
function safePath(p) {
  const parts = String(p ?? '')
    .replace(/\\/g, '/')
    .split('/')
    .map((s) => s.replace(/[^\w.\- ]+/g, '').trim())
    .filter((s) => s && s !== '.' && s !== '..');
  return parts.join('/').slice(0, 200);
}

function safeFilename(filename) {
  const name = String(filename ?? '')
    .split(/[\\/]/)
    .pop()
    .replace(/[^\w.\- ]+/g, '')
    .trim()
    .slice(0, 100);
  if (!name || name.startsWith('.')) return `file${name || '.txt'}`;
  return /\.[a-z0-9]+$/i.test(name) ? name : `${name}.txt`;
}

function setTier({ person, tier }) {
  if (!TIERS[tier]) return `Error: tier must be one of ${Object.keys(TIERS).join(', ')}`;
  const contact = findContact(person);
  if (!contact) return `Error: "${person}" isn't on your contact list. They need to talk to you at least once first.`;
  if (isOwner(contact.user_id)) return `${contact.name} is your owner; their access can't be changed.`;
  const before = contact.tier;
  setContactTier(contact.user_id, tier);
  activity('contact', { name: contact.name, userId: contact.user_id, from: before, tier });
  return before === tier ? `${contact.name} was already a ${tier}.` : `${contact.name} is now a ${tier} (was ${before}).`;
}

function showContacts({ tier } = {}) {
  const rows = listContacts()
    .map((c) => ({ ...c, tier: tierOf(c.user_id) }))
    .filter((c) => !tier || c.tier === tier);
  if (!rows.length) return tier ? `Nobody is a ${tier}.` : 'Your contact list is empty.';
  return rows
    .map((c) => `- ${c.name}${c.username && c.username !== c.name ? ` (@${c.username})` : ''}: ${c.tier}${c.last_dm_at ? ', has DMed you' : ''}`)
    .join('\n');
}
