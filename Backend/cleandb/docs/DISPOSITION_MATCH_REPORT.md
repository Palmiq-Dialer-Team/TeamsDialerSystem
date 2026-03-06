# Disposition File vs Database Match — Report

This report explains the results of `diagnose_disposition_match.py` and what they mean for your repair and file coverage.

---

## 1. Summary

| Metric | Value |
|--------|--------|
| **Total call rows in database** | 13,483 |
| **Unique (name, phone) keys in your files** | 5,551 |
| **DB rows that matched a file key** | 2,332 |
| **DB rows that did NOT match** | 11,151 |

So: the database has **about 2.4× more call rows** than unique contact+phone combinations in your disposition files, and only **2,332** of those DB rows matched a file key. The rest are either not in your files, or failed to match because of name/phone rules or bad files.

---

## 2. File Read Errors (Fix These First)

Two Excel files could not be read and were skipped:

- **Invalid Contact.xlsx** — `Error -3 while decompressing data: invalid distance too far back`
- **Voicemail (Verified) (3).xlsx** — `Bad magic number for central directory`

**What this means:** The files are likely corrupted, truncated, or saved in a way that openpyxl cannot read (e.g. not a valid xlsx zip structure).

**What to do:**

1. Open each file in Excel and **Save As** a new `.xlsx` (or export to CSV and re-save as xlsx).
2. If the file was copied from email/network, copy again or re-download to avoid corruption.
3. Re-run the diagnostic and repair after fixing or replacing these files so those dispositions are included.

Until they are fixed, any contacts that exist **only** in these two files will never match and will contribute to “file keys that no DB row had” and “DB rows that didn’t match.”

---

## 3. Why DB Rows Did Not Match a File Key

The repair script matches on **(name_norm, phone_norm)**. Each DB row is classified into one of these buckets:

| Reason | Count | Meaning |
|--------|--------|--------|
| **Matched (name+phone)** | 2,332 | DB row had the same normalized name and phone as a file row. These are the ones the repair script can update. |
| **No phone** | 0 | No DB rows with empty phone. |
| **No name** | 35 | DB has a phone and call info but **no contact name** (empty `contact_data` and `contact_name`). The repair script **requires** a name to match, so these 35 are skipped. |
| **Phone NOT in any file** | **11,103** | The phone number in the DB never appears in any of your disposition files (after normalization). |
| **Phone IN file but NAME mismatch** | 13 | The phone exists in the files, but the First/Last name in the DB does not match the name in the file (spelling, spacing, or format). |

So the main driver of “no match” is: **11,103 DB rows have a phone that is not present in your files at all.** Second is the 35 with no name; then the 13 name mismatches.

---

## 4. Why So Many “Phone NOT in any file”?

Possible explanations:

1. **Different lead lists / campaigns**  
   The DB contains calls from campaigns or lead lists that were never exported into the disposition files you put in `disposition_files`. So those numbers simply don’t exist in the files.

2. **Files are a subset**  
   Your disposition files might be exports for a specific agent, date range, or disposition type. The DB has 13k+ calls across all agents and time; if the files only cover part of that, most DB phones won’t be in the files.

3. **Different phone columns**  
   The diagnostic (and repair) script reads **all** columns whose header contains “phone” (except “company phone”). If your files use different column names or store numbers in a column that doesn’t contain “phone,” those numbers won’t be loaded and the DB will show as “phone not in any file.”

4. **Corrupted/skipped files**  
   The two skipped xlsx files (Invalid Contact, Voicemail (Verified) (3)) reduce the set of phones in the “file side.” Any number that appears only in those files will count as “phone not in any file” for the DB.

5. **Format/encoding**  
   Less common, but if files have leading zeros, country codes, or characters that get stripped differently than in the DB, normalization could theoretically miss matches. The script normalizes to digits-only; if the file has the same digits as the DB, they should match.

So the 11,103 “phone not in any file” is best interpreted as: **these calls exist in the DB but their phone number does not appear in any of the (successfully loaded) disposition file rows.**

---

## 5. File Side: Keys That Never Matched a DB Row

| Metric | Count |
|--------|--------|
| File (name, phone) keys that **at least one** DB row matched | 2,126 |
| File (name, phone) keys that **no** DB row had | 3,425 |

So you have **3,425** (name, phone) combinations in your files that never appear in the database. That usually means:

- Those contacts were **never called** in this system (files include leads that weren’t dialed), or  
- They were called from a **different DB / environment** (e.g. another server or an old DB), or  
- The **phone format or name** in the file doesn’t match how it was stored when the call was made (e.g. different column used when logging the call).

So the “14k” feeling you had is a combination of:

- **11,103** DB rows whose phone isn’t in the files at all,  
- **35** DB rows with no name,  
- **13** phone-in-file but name mismatch,  
- Plus the **3,425** file keys that don’t appear in the DB.

The repair script only **updates** DB rows; it doesn’t create rows. So the 2,332 that matched are the only ones that could be fixed by the repair script. The 11k+ are “no file data for this phone”; the 3,425 are “file data but no call in this DB.”

---

## 6. The 35 Rows With No Name

Sample: `id=14757 agent=Rafael phone_norm='3036686377' contact_name=''`.

These rows have a phone and call metadata but **no** `contact_data` (or empty) and **no** `contact_name`. The repair script **requires** a name to form the (name_norm, phone_norm) key, so it never matches them.

**Recommendation:** Run the **backfill** script (`backfill_calls_from_excel.py`) with lead files that contain these phones (and names). That will fill `contact_data` so future repair runs can match them. After backfill, re-run the diagnostic to confirm the “no name” count drops.

---

## 7. The 13 “Phone in file but name mismatch”

Same phone in DB and in file, but the **name** (First + Last) differs after normalization (e.g. “Paul N” vs “Paul Smith,” or extra spaces). So (name_norm, phone_norm) doesn’t match.

**Recommendation:** If you want to repair by **phone only** (ignore name for matching), the repair script can be changed to match only on `phone_norm`. Then these 13 would be eligible for repair. The diagnostic already supports this interpretation; the repair script would need a small change to use phone-only when you want it.

---

## 8. One Odd Sample: `phone_norm='Researching'`

In “Phone IN file but NAME mismatch” you have:

`id=13862 agent=Rafael phone_norm='Researching' name_norm='carl vogel'`

So the DB has a “phone” value that is literally the word **Researching**, not a number. That suggests the lead list or UI allowed a non-phone value in the phone field. The script normalizes it to digits; “Researching” becomes empty (or is kept as-is depending on implementation), so it won’t match a real number in the file. Cleaning the source data so that column only contains phone numbers (or marking such rows so they’re not used for matching) would avoid this kind of noise.

---

## 9. Recommendations Checklist

1. **Fix the two bad Excel files**  
   Re-save or re-export **Invalid Contact.xlsx** and **Voicemail (Verified) (3).xlsx** so they open correctly. Then re-run the diagnostic (and repair if needed).

2. **Run backfill for the 35 rows with no name**  
   Use `backfill_calls_from_excel.py` with lead files that contain those phones so `contact_data` (and thus name) is filled. Then run the diagnostic again to confirm.

3. **Decide scope of disposition files**  
   If the 11,103 “phone not in file” are from other campaigns or older data, either:  
   - Add exports for those campaigns/periods into `disposition_files`, or  
   - Accept that only the 2,332 (and the 35 after backfill, plus 13 if you switch to phone-only) can be updated from **current** files.

4. **Optional: phone-only matching**  
   To also repair the 13 “phone in file, name mismatch” rows, change the repair script to match on **phone only** (ignore name). Then re-run repair; the diagnostic already shows which are name-mismatch.

5. **Data quality**  
   Fix or exclude non-phone values (e.g. “Researching”) in the phone field so they don’t create confusing matches or no-matches.

---

## 10. What the Numbers Mean in Plain Terms

- **2,332** — DB rows that matched your files and could be (or were) updated by the repair script.  
- **11,103** — DB rows whose phone number doesn’t appear in any of your current disposition files; repair can’t fix these without adding more file data.  
- **35** — DB rows with no name; fix by backfilling `contact_data`.  
- **13** — DB rows where the phone is in the file but the name doesn’t match; fix by phone-only matching or by aligning names.  
- **3,425** — File (name, phone) keys that don’t exist in the DB; they’re “extra” in the files (never called in this DB or different environment).  
- **2 corrupted xlsx files** — Fix so their dispositions are included in future runs.

If you want, the next step can be: (1) add phone-only mode to the repair script, and (2) document exactly which columns the script expects so you can align your exports and avoid “phone not in any file” where the data actually exists under a different column name.
