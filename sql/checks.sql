-- Run in Athena (workgroup rde-dev, database rde_dev).

-- 1. Runs and clean output per run
SELECT run_id, count(*) AS clean_frames FROM episode_frames GROUP BY run_id ORDER BY run_id DESC;

-- 2. Episodes kept vs rejected, with reasons
SELECT status, reasons, count(*) AS episodes FROM episodes GROUP BY status, reasons ORDER BY episodes DESC;

-- 3. What the cleaning removed, by reason
SELECT entity, reason, count(*) AS n FROM quarantine GROUP BY entity, reason ORDER BY n DESC;

-- 4. Clean extracted frames per episode
SELECT episode_index, count(*) AS frames, round(avg(blur_score), 1) AS avg_blur
FROM video_frames GROUP BY episode_index ORDER BY episode_index LIMIT 20;

-- 5. Time travel: the table as of an earlier snapshot (reproducible training sets)
SELECT * FROM "episode_frames$snapshots" ORDER BY committed_at DESC;
