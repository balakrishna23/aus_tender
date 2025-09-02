#!/usr/bin/env python3
"""
CSV to Database Import Tool
Imports existing CSV data into the appropriate SQLite database
"""

import pandas as pd
import argparse
import sys
from pathlib import Path
from database_manager import DatabaseManager
from datetime import datetime


def import_csv_to_database(csv_file: str, spider_name: str):
    """Import CSV data to the appropriate database"""
    
    print(f"🚀 Importing {csv_file} to {spider_name} database...")
    
    # Check if CSV file exists
    if not Path(csv_file).exists():
        print(f"❌ Error: CSV file '{csv_file}' not found!")
        return False
    
    try:
        # Read CSV file
        print(f"📖 Reading CSV file: {csv_file}")
        df = pd.read_csv(csv_file)
        print(f"📊 Found {len(df)} records in CSV")
        
        # Initialize database manager
        db_manager = DatabaseManager()
        
        # Initialize database schema
        print(f"🗄️ Initializing {spider_name} database...")
        db_manager.initialize_database(spider_name)
        
        # Import each row
        success_count = 0
        error_count = 0
        
        print(f"💾 Importing records...")
        for index, row in df.iterrows():
            # Convert pandas Series to dictionary
            item_data = row.to_dict()
            
            # Add timestamp
            item_data['scraped_at'] = datetime.now().isoformat()
            
            # Import to database
            success = db_manager.insert_item(spider_name, item_data)
            
            if success:
                success_count += 1
            else:
                error_count += 1
            
            # Progress indicator
            if (index + 1) % 100 == 0:
                print(f"  📈 Processed {index + 1}/{len(df)} records...")
        
        # Final statistics
        print(f"\n✅ Import completed!")
        print(f"   📊 Total records processed: {len(df)}")
        print(f"   ✅ Successfully imported: {success_count}")
        print(f"   ❌ Failed imports: {error_count}")
        
        # Get database stats
        stats = db_manager.get_stats(spider_name)
        print(f"   🗄️ Total records in database: {stats.get('total_records', 0)}")
        print(f"   📁 Database file: {stats.get('database_file', 'Unknown')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during import: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Import CSV data to spider database')
    parser.add_argument('csv_file', help='Path to CSV file')
    parser.add_argument('--spider', required=True, 
                       choices=['aus_contract', 'aus_tender', 'planned_procurement', 'standing_offer'],
                       help='Spider name (determines which database to use)')
    
    args = parser.parse_args()
    
    success = import_csv_to_database(args.csv_file, args.spider)
    
    if success:
        print(f"\n🎉 CSV data successfully imported to {args.spider} database!")
        print(f"\n💡 You can now query the data using:")
        print(f"   python db_query_tool.py --stats {args.spider}")
        print(f"   python db_query_tool.py --info {args.spider}")
    else:
        print(f"\n❌ Import failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()