# InfoMentor for Home Assistant

> ### ⚠️ Under active development
>
> This is an early, unofficial integration built by reverse engineering the
> InfoMentor web client. It works, but expect breaking changes, renamed
> entities and rough edges between releases. InfoMentor can change their
> undocumented endpoints at any time and break it without warning.
>
> Use it at your own risk, and please open an issue if something breaks - bug
> reports from other municipalities are very welcome.

Custom integration that pulls school information from the InfoMentor hub
(`hub.infomentor.se`) into Home Assistant. Each pupil becomes a **device**, with
sensors and a calendar entity underneath it.

Developed against a Swedish municipal account. Other municipalities use the
same hub, but module availability varies per school.

## Credits

The login handshake was worked out from
[kolplattformen/dementor.net](https://github.com/kolplattformen/dementor.net),
a C# lab that first mapped the InfoMentor API. Full credit to that project for
the groundwork - this integration would have been far harder without it.

## Installation (HACS)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add this repository, category **Integration**
3. Install **InfoMentor**, restart Home Assistant
4. Settings → Devices & services → Add integration → **InfoMentor**

## Entities

Per pupil:

| Entity | Description |
| --- | --- |
| `sensor.<pupil>_next_lesson` | Timestamp of the next lesson; room and teacher as attributes |
| `sensor.<pupil>_unread_notifications` | Count of unread notifications; the unread items are listed in attributes |
| `sensor.<pupil>_latest_learnlog_post` | Timestamp of the newest learnlog post, with image list |
| `sensor.<pupil>_registered_absences` | Number of registered absences |
| `sensor.<pupil>_scheduled_time_today` | Registered childcare times today, e.g. `07:00-17:00` |
| `binary_sensor.<pupil>_checked_in` | On while the child is checked in at school |
| `binary_sensor.<pupil>_pickup_comment_missing` | On when today has no parent comment |
| `calendar.<pupil>_schedule` | Timetable plus calendar entries |

Modules that a pupil does not have (a preschooler has no timetable) are skipped
rather than reported as errors. Which modules to fetch is configurable per pupil
in the integration options, so you can turn off learnlog for a child whose
teacher never posts.

## Pickup comment reminder

In the time registration view parents leave a note such as "får gå hem själv".
`binary_sensor.<pupil>_pickup_comment_missing` turns **on** when today's
registration has no parent comment, which makes the reminder a plain state
trigger:

```yaml
automation:
  - alias: Remind me about the pickup comment
    triggers:
      - trigger: state
        entity_id: binary_sensor.pupil_pickup_comment_missing
        to: "on"
        for: "00:10:00"
    conditions:
      - condition: time
        after: "06:00:00"
        before: "08:00:00"
    actions:
      - action: notify.mobile_app
        data:
          message: "No pickup comment registered today"
```

The entity is unavailable on days off, when the school is closed, and for pupils
without the time registration module, so it will not nag on weekends.

## Service: `infomentor.set_time_registration_comment`

This service writes a parent comment back to the InfoMentor time registration
view. Pick one or more pupil devices, optionally choose a date, and enter the
comment text:

```yaml
action: infomentor.set_time_registration_comment
data:
  device_id: ["<pupil device>"]
  date: "2026-09-02"
  comment: "Can go home by herself"
```

`date` is optional and defaults to today. The service refreshes the integration
after saving so `binary_sensor.<pupil>_pickup_comment_missing` updates quickly.

This sends data to the school system and the comment may be visible to school
staff.

## Events

| Event | Fired when |
| --- | --- |
| `infomentor_new_learnlog_media` | A new learnlog image appears |
| `infomentor_new_calendar_attachment` | A new calendar attachment (weekly letter PDF) appears |

Event data: `pupil_id`, `pupil_name`, `file_id`, `filename`, `entry_id`,
`entry_title`, `entry_date`, `path`. `path` is the saved file when automatic
download is enabled, otherwise `null`.

No events fire on the first refresh after installation, so an existing backlog
does not trigger a flood of automations.

## Automatic download

Enable **Automatically download new photos and letters** in the integration
options and every new file is fetched as it appears:

```
<download folder>/Anna_Andersson/photos/individual/2026-08-24_IMG_6118.jpeg
<download folder>/Anna_Andersson/photos/group/Blabaret/2026-09-01_IMG_6120.jpeg
<download folder>/Anna_Andersson/letters/2026-08-28_Veckobrev_v.36.pdf
```

Names are made filesystem safe: `Andersson, Anna` becomes `Anna_Andersson`,
accents are folded to ASCII and spaces become underscores, so the paths work on
SMB shares and other filesystems.

Posts written about your own child land in `photos/individual`, while posts for
the whole group go in `photos/group/<group name>`, so the two are easy to tell
apart. The event payload carries the same information as `scope` and
`group_name`.

The default folder is `/media/infomentor`, which Home Assistant serves through
the authenticated media browser. Any other location must be listed in
`allowlist_external_dirs`.

Avoid `config/www` (`/local/...`): that folder is served **without**
authentication, so anyone who can reach your Home Assistant URL could read the
children's photos.

Learnlog photos cover both individual posts and group posts. Files are recorded
by id, so a file is downloaded only once even if the post is edited later.

## Service: `infomentor.download_backlog`

Automatic download only covers posts that appear after installation. To archive
older material - for example a preschool portfolio before the child leaves -
call `infomentor.download_backlog` once with a date range:

```yaml
action: infomentor.download_backlog
data:
  start_date: "2023-01-01"
  end_date: "2026-09-01"
  pupil_id: ["1234567"]
  sources: ["photos"]
  path: /media/infomentor
  limit: 10
```

Pick pupils with the **Pupils** device selector in the UI. All fields except
`start_date` are optional; pupils default to all, sources to photos and letters,
and the path to the configured download folder. The service returns
`{"downloaded": n, "failed": n}`.

Start with a small `limit` to confirm the files land where you expect - a full
backlog can be several hundred files and takes a while.

## Service: `infomentor.download_file`

InfoMentor file URLs carry no token — they only work on the integration's
logged-in session, so a generic downloader cannot fetch them. This service does
the authenticated download and writes the file to disk; anything further
(Google Drive, Nextcloud, notifications) belongs in your own automation.

```yaml
automation:
  - alias: Archive new preschool photos
    triggers:
      - trigger: event
        event_type: infomentor_new_learnlog_media
    actions:
      - action: infomentor.download_file
        data:
          file_id: "{{ trigger.event.data.file_id }}"
      - action: notify.mobile_app
        data:
          message: "New photo: {{ trigger.event.data.entry_title }}"
```

`path` is optional and defaults to the configured download folder, or
`/media/infomentor` if no folder is configured. When overridden, the target
directory must be listed under `allowlist_external_dirs` in `configuration.yaml`,
or be under `/media`.

## Notes

InfoMentor selects the active pupil through server-side session state, so the
integration fetches pupils strictly one at a time. Polling defaults to 30
minutes and is configurable in the integration options.

This is an unofficial integration and is not affiliated with InfoMentor.
