#!/usr/bin/env python3
"""
Database Query Tool for Australian Government Procurement Data
Provides easy access to query and analyze data from all spider databases
"""

import sqlite3
import json
import argparse
import pandas as pd
from pathlib import Path
from database_manager import DatabaseManager
from typing import Optional, List, Dict, Any


class DatabaseQueryTool:
    """Tool for querying and analyzing spider databases"""
    
    def __init__(self, db_directory: Optional[str] = None):
        self.db_manager = DatabaseManager(db_directory)
        self.db_directory = self.db_manager.db_directory
    
    def list_databases(self) -> List[str]:
        """List all available databases"""
        if not self.db_directory.exists():
            return []
        
        db_files = list(self.db_directory.glob("*.db"))
        return [db.stem for db in db_files]
    
    def get_database_info(self, spider_name: str) -> Dict[str, Any]:
        """Get comprehensive database information"""
        try:
            stats = self.db_manager.get_stats(spider_name)
            
            with self.db_manager.get_connection(spider_name) as conn:
                # Get table schema
                table_name = self.db_manager.SCHEMAS[spider_name]['table_name']
                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [{'name': row[1], 'type': row[2]} for row in cursor.fetchall()]
                
                # Get sample data
                cursor = conn.execute(f"SELECT * FROM {table_name} LIMIT 5")
                sample_data = [dict(row) for row in cursor.fetchall()]
                
                return {
                    'stats': stats,
                    'columns': columns,
                    'sample_data': sample_data
                }
        except Exception as e:
            return {'error': str(e)}
    
    def query_database(self, spider_name: str, query: str) -> List[Dict[str, Any]]:
        """Execute custom SQL query"""
        try:
            with self.db_manager.get_connection(spider_name) as conn:
                cursor = conn.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Query error: {e}")
            return []
    
    def export_to_csv(self, spider_name: str, output_file: str, query: Optional[str] = None):
        """Export data to CSV"""
        try:
            if query is None:
                table_name = self.db_manager.SCHEMAS[spider_name]['table_name']
                query = f"SELECT * FROM {table_name}"
            
            with self.db_manager.get_connection(spider_name) as conn:
                df = pd.read_sql_query(query, conn)
                df.to_csv(output_file, index=False)
                print(f"Data exported to {output_file}")
                print(f"Exported {len(df)} records")
        except Exception as e:
            print(f"Export error: {e}")
    
    def get_summary_stats(self, spider_name: str) -> Dict[str, Any]:
        """Get summary statistics for a database"""
        try:
            table_name = self.db_manager.SCHEMAS[spider_name]['table_name']
            
            with self.db_manager.get_connection(spider_name) as conn:
                stats = {}
                
                # Total records
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                stats['total_records'] = cursor.fetchone()[0]
                agency_column = 'buyer' if spider_name == 'buy_ict' else 'agency'
                
                # Records by agency (top 10)
                cursor = conn.execute(f"""
                    SELECT {agency_column} AS agency, COUNT(*) as count
                    FROM {table_name} 
                    WHERE {agency_column} IS NOT NULL
                    GROUP BY {agency_column}
                    ORDER BY count DESC 
                    LIMIT 10
                """)
                stats['top_agencies'] = [dict(row) for row in cursor.fetchall()]
                
                # Records by date (if applicable)
                date_column = 'publish_date'
                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [row[1] for row in cursor.fetchall()]
                
                if date_column in columns:
                    cursor = conn.execute(f"""
                        SELECT DATE(publish_date) as date, COUNT(*) as count 
                        FROM {table_name} 
                        WHERE publish_date IS NOT NULL 
                        GROUP BY DATE(publish_date) 
                        ORDER BY date DESC 
                        LIMIT 10
                    """)
                    stats['recent_activity'] = [dict(row) for row in cursor.fetchall()]
                
                return stats
        except Exception as e:
            return {'error': str(e)}


def main():
    parser = argparse.ArgumentParser(description='Database Query Tool for Australian Procurement Data')
    parser.add_argument('--list', action='store_true', help='List all databases')
    parser.add_argument('--info', type=str, help='Get database info for spider')
    parser.add_argument('--query', type=str, help='Execute custom SQL query')
    parser.add_argument('--spider', type=str, help='Spider name for query/export')
    parser.add_argument('--export', type=str, help='Export to CSV file')
    parser.add_argument('--stats', type=str, help='Get summary statistics for spider')
    
    args = parser.parse_args()
    
    tool = DatabaseQueryTool()
    
    if args.list:
        databases = tool.list_databases()
        print("Available databases:")
        for db in databases:
            print(f"  - {db}")
    
    elif args.info:
        info = tool.get_database_info(args.info)
        print(f"\nDatabase Info for {args.info}:")
        print(json.dumps(info, indent=2, default=str))
    
    elif args.stats:
        stats = tool.get_summary_stats(args.stats)
        print(f"\nSummary Statistics for {args.stats}:")
        print(json.dumps(stats, indent=2, default=str))
    
    elif args.query and args.spider:
        results = tool.query_database(args.spider, args.query)
        print(f"\nQuery Results ({len(results)} records):")
        for result in results[:10]:  # Show first 10 results
            print(json.dumps(result, indent=2, default=str))
        if len(results) > 10:
            print(f"... and {len(results) - 10} more records")
    
    elif args.export and args.spider:
        tool.export_to_csv(args.spider, args.export)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
