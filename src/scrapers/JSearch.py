import os
import requests
import pandas as pd

from datetime import datetime
from dotenv import load_dotenv


# ==========================================
# 1. Load API Key
# ==========================================

load_dotenv()

api_key = os.getenv("JSEARCH_API_KEY")

if not api_key:
    raise ValueError("JSEARCH_API_KEY not found in .env")


# ==========================================
# 2. API Settings
# ==========================================

url = "https://api.openwebninja.com/jsearch/search-v2"

headers = {
    "x-api-key": api_key
}


# ==========================================
# 3. Queries
# ==========================================

queries = {
    "Data Engineer": "Data Engineer in Saudi Arabia",
    "Software Engineer": "Software Engineer in Saudi Arabia",
    "Backend Developer": "Backend Developer in Saudi Arabia",
    "Frontend Developer": "Frontend Developer in Saudi Arabia",
    "AI Engineer": "AI Engineer in Saudi Arabia"
}


# ==========================================
# 4. Maximum pages per query
# ==========================================

MAX_PAGES_PER_QUERY = 10


# This will contain all jobs
all_jobs = []


# ==========================================
# 5. Extract Jobs
# ==========================================

for target_role, query in queries.items():

    print("\n==========================================")
    print("Searching for:", target_role)
    print("Query:", query)
    print("==========================================")

    params = {
        "query": query,
        "country": "sa",
        "language": "en"
    }

    cursor = None
    page = 1


    while page <= MAX_PAGES_PER_QUERY:

        print(f"\nFetching {target_role} - Page {page}...")

        # Add cursor for the next page
        if cursor:
            params["cursor"] = cursor

        try:

            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=30
            )

        except requests.RequestException as error:

            print("Request error:", error)
            break


        print("Status Code:", response.status_code)


        # Stop if request failed
        if response.status_code != 200:

            print("Request failed.")

            try:
                print(response.json())
            except Exception:
                print(response.text)

            break


        # Convert API response to JSON
        data = response.json()

        result = data.get("data", {})

        jobs = result.get("jobs", [])


        # Stop if there are no jobs
        if not jobs:

            print("No more jobs found for:", target_role)
            break


        # ==========================================
        # Add extra fields
        # ==========================================

        for job in jobs:

            job["target_role"] = target_role

            job["search_query"] = query

            job["collected_at"] = datetime.now().isoformat()


        # Add jobs to main list
        all_jobs.extend(jobs)


        print("Jobs in this page:", len(jobs))
        print("Total jobs fetched this run:", len(all_jobs))


        # Get cursor for next page
        cursor = result.get("cursor")


        # If no cursor, there is no next page
        if not cursor:

            print("No more pages available for:", target_role)
            break


        page += 1


# ==========================================
# 6. Convert Jobs to DataFrame
# ==========================================

new_df = pd.DataFrame(all_jobs)


if new_df.empty:

    print("\nNo jobs were collected.")
    exit()


# ==========================================
# 7. File Path
# ==========================================

file_path = "data/raw/jobs_sa.csv"


# Create folders if they don't exist
os.makedirs(
    "data/raw",
    exist_ok=True
)


# ==========================================
# 8. Incremental Loading
# ==========================================

if os.path.exists(file_path):

    print("\nExisting CSV found.")

    old_df = pd.read_csv(file_path)

    print("Old jobs:", len(old_df))


    # Combine old + newly fetched jobs
    combined_df = pd.concat(
        [old_df, new_df],
        ignore_index=True
    )


    # ==========================================
    # Remove Duplicate Jobs
    # ==========================================

    before_duplicates = len(combined_df)

    combined_df = combined_df.drop_duplicates(
        subset=["job_uid"],
        keep="first"
    )

    after_duplicates = len(combined_df)

    duplicates_removed = (
        before_duplicates - after_duplicates
    )


else:

    print("\nNo existing CSV found.")

    combined_df = new_df

    duplicates_removed = 0


# ==========================================
# 9. Save Raw Data
# ==========================================

combined_df.to_csv(
    file_path,
    index=False
)


# ==========================================
# 10. Summary
# ==========================================

print("\n==========================================")
print("Finished successfully ✅")
print("==========================================")

print(
    "Jobs fetched this run:",
    len(new_df)
)

print(
    "Duplicates removed:",
    duplicates_removed
)

print(
    "Total unique jobs saved:",
    len(combined_df)
)

print(
    "File:",
    file_path
)


# ==========================================
# 11. Jobs Per Target Role
# ==========================================

print("\nJobs fetched by target role:")

print(
    new_df["target_role"]
    .value_counts()
)