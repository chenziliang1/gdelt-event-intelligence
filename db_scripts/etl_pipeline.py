#!/usr/bin/env python3
"""
GDELT ETL Pipeline
Purpose: precalculatedayreportdata、generate event fingerprints、updatestatisticsdata
runfrequency: eachdayonetime（builddiscussearly morning2point）

Usage:
    python db_scripts/etl_pipeline.py [YYYY-MM-DD]
    
    nottransmitargumentsruleprocessyesterdaydaydata
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# add project path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database.pool import DatabasePool
from backend.queries.query_utils import FORECAST_EVENT_TYPE_CONDITIONS
sys.path.insert(0, os.path.dirname(__file__))
from precompute_sql import HOT_FINGERPRINTS_SQL, insert_select_sql, region_stats_sql  # noqa: E402

# configure log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/gdelt_etl.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class GDELTETLPipeline:
    """GDELTdataETLpipeline"""
    
    def __init__(self):
        self.pool: Optional[DatabasePool] = None
        
    async def initialize(self):
        """initialize database connection"""
        self.pool = await DatabasePool.initialize()
        logger.info("✅ databaseconnectionpoolalreadyinitialstartization")
    
    async def close(self):
        """close connection"""
        await DatabasePool.close()
        logger.info("✅ databaseconnectionalreadyclose")
    
    async def run_daily_etl(self, target_date: Optional[str] = None):
        """
        runeachdayETLtask
        
        Args:
            target_date: projectmarkdate (YYYY-MM-DD)，defaultyesterdayday
        """
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        logger.info(f"🚀 startETLprocess: {target_date}")
        
        try:
            # 1. checkthisdatewhetherhasdata
            has_data = await self._check_data_exists(target_date)
            if not has_data:
                logger.warning(f"⚠️ {target_date} no data，skipETL")
                return
            
            # 2. generate daily digest
            await self._generate_daily_summary(target_date)
            
            # 3. generateneweventfingerprint
            await self._generate_event_fingerprints(target_date)
            
            # 4. update region statistics
            await self._update_region_stats(target_date)
            
            # 5. update geo grid
            await self._update_geo_grid(target_date)
            
            # 6. identify hot eventsandupdatefingerprintreference
            await self._identify_hot_events(target_date)
            
            logger.info(f"✅ ETLcompleted: {target_date}")
            
        except Exception as e:
            logger.error(f"❌ ETLfailed: {e}", exc_info=True)
            raise
    
    async def _check_data_exists(self, date: str) -> bool:
        """checkfingerfixdatewhetherhasdata"""
        result = await self.pool.fetchone(
            "SELECT COUNT(*) as cnt FROM events_table WHERE SQLDATE = %s",
            (date,)
        )
        count = result['cnt'] if result else 0
        logger.info(f"📊 {date} dataamount: {count} item")
        return count > 0
    
    async def _generate_daily_summary(self, date: str):
        """generate daily digesttable"""
        logger.info(f"📊 generatedayreport: {date}")
        
        # Same definitions as the stored 2024 daily_summary (verified: Goldstein < 0 / > 0) and as
        # the forecaster's global series. This ETL used |Goldstein| > 5, so new days would not have
        # matched the history.
        c = FORECAST_EVENT_TYPE_CONDITIONS
        stats = await self.pool.fetchone(f"""
            SELECT 
                COUNT(*) as total_events,
                SUM(CASE WHEN {c['conflict']} THEN 1 ELSE 0 END) as conflict_events,
                SUM(CASE WHEN {c['cooperation']} THEN 1 ELSE 0 END) as cooperation_events,
                AVG(GoldsteinScale) as avg_goldstein,
                AVG(AvgTone) as avg_tone
            FROM events_table
            WHERE SQLDATE = %s
        """, (date,))
        
        if not stats or stats['total_events'] == 0:
            logger.warning(f"  ⚠️ {date} no data")
            return
        
        # fetchTop Actor
        actors_result = await self.pool.fetchall("""
            SELECT Actor1Name as name, COUNT(*) as cnt
            FROM events_table
            WHERE SQLDATE = %s AND Actor1Name != '' AND Actor1Name IS NOT NULL
            GROUP BY Actor1Name
            ORDER BY cnt DESC
            LIMIT 10
        """, (date,))
        
        top_actors = [{"name": row['name'], "count": row['cnt']} for row in actors_result]
        
        # fetchTop Location
        locations_result = await self.pool.fetchall("""
            SELECT ActionGeo_FullName as name, COUNT(*) as cnt
            FROM events_table
            WHERE SQLDATE = %s AND ActionGeo_FullName IS NOT NULL AND ActionGeo_FullName != ''
            GROUP BY ActionGeo_FullName
            ORDER BY cnt DESC
            LIMIT 10
        """, (date,))
        
        top_locations = [{"name": row['name'], "count": row['cnt']} for row in locations_result]
        
        # eventtypedistribution
        types_result = await self.pool.fetchall("""
            SELECT 
                CASE 
                    WHEN EventRootCode = '01' THEN 'statement'
                    WHEN EventRootCode = '02' THEN 'appeal'
                    WHEN EventRootCode = '03' THEN 'intent'
                    WHEN EventRootCode IN ('04', '05') THEN 'consult'
                    WHEN EventRootCode = '06' THEN 'material'
                    WHEN EventRootCode IN ('07', '08') THEN 'aid'
                    WHEN EventRootCode = '09' THEN 'yield'
                    WHEN EventRootCode = '10' THEN 'demand'
                    WHEN EventRootCode = '11' THEN 'disapprove'
                    WHEN EventRootCode = '12' THEN 'reject'
                    WHEN EventRootCode = '13' THEN 'threaten'
                    WHEN EventRootCode = '14' THEN 'protest'
                    WHEN EventRootCode = '15' THEN 'force'
                    WHEN EventRootCode IN ('16', '17') THEN 'coerce'
                    WHEN EventRootCode IN ('18', '19', '20') THEN 'fight'
                    ELSE 'other'
                END as event_type,
                COUNT(*) as cnt
            FROM events_table
            WHERE SQLDATE = %s
            GROUP BY event_type
            ORDER BY cnt DESC
        """, (date,))
        
        type_dist = {row['event_type']: row['cnt'] for row in types_result}
        
        # hoteventfingerprint（temporarywhenuseGID，aftercontinueupdateforfingerprint）
        hot_result = await self.pool.fetchall("""
            SELECT GlobalEventID, NumArticles * ABS(GoldsteinScale) as hot_score
            FROM events_table
            WHERE SQLDATE = %s
            ORDER BY hot_score DESC
            LIMIT 20
        """, (date,))
        
        hot_events = [str(row['GlobalEventID']) for row in hot_result]
        
        # insert/updatedayreport
        await self.pool.execute("""
            INSERT INTO daily_summary 
            (date, total_events, conflict_events, cooperation_events,
             avg_goldstein, avg_tone, top_actors, top_locations,
             event_type_distribution, hot_event_fingerprints)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            total_events = VALUES(total_events),
            conflict_events = VALUES(conflict_events),
            cooperation_events = VALUES(cooperation_events),
            avg_goldstein = VALUES(avg_goldstein),
            avg_tone = VALUES(avg_tone),
            top_actors = VALUES(top_actors),
            top_locations = VALUES(top_locations),
            event_type_distribution = VALUES(event_type_distribution),
            hot_event_fingerprints = VALUES(hot_event_fingerprints)
        """, (
            date, 
            stats['total_events'], 
            stats['conflict_events'] or 0, 
            stats['cooperation_events'] or 0,
            stats['avg_goldstein'], 
            stats['avg_tone'],
            json.dumps(top_actors),
            json.dumps(top_locations),
            json.dumps(type_dist),
            json.dumps(hot_events)
        ))
        
        logger.info(f"  ✓ dayreportalreadygenerate: {stats['total_events']} event, {len(top_actors)} activeActor")
    
    async def _generate_event_fingerprints(self, date: str):
        """Fingerprints for the day's events, in one INSERT ... SELECT (see precompute_sql.py).

        The previous version inserted row by row (days for the 2024 backfill), produced mangled
        headlines, and could collide on the UNIQUE fingerprint (it used the last 3 ID digits).
        """
        logger.info(f"🔖 generate event fingerprints: {date}")
        affected = await self.pool.execute(insert_select_sql("e.SQLDATE = %s"), (date,))
        logger.info(f"  ✓ fingerprints written: {affected}")

    async def _update_region_stats(self, date: str):
        """Countries and first-level divisions for the day (see precompute_sql.region_stats_sql)."""
        logger.info(f"🌍 update region statistics: {date}")
        for region_type in ("country", "state"):
            affected = await self.pool.execute(region_stats_sql(region_type), (date, date) * 3)
            logger.info(f"  ✓ {region_type} rows written: {affected}")

    async def _update_geo_grid(self, date: str):
        """update geo gridhot"""
        logger.info(f"🗺️ update geo grid: {date}")
        
        # by0.5schedulegridaggregate
        grids = await self.pool.fetchall("""
            SELECT 
                FLOOR(ActionGeo_Lat * 2) / 2 as lat_grid,
                FLOOR(ActionGeo_Long * 2) / 2 as lng_grid,
                COUNT(*) as event_count,
                SUM(CASE WHEN GoldsteinScale < -5 THEN 1 ELSE 0 END) as conflict_sum,
                AVG(GoldsteinScale) as avg_goldstein,
                AVG(AvgTone) as avg_tone
            FROM events_table
            WHERE SQLDATE = %s 
              AND ActionGeo_Lat IS NOT NULL 
              AND ActionGeo_Long IS NOT NULL
              AND ActionGeo_Lat != 0
              AND ActionGeo_Long != 0
            GROUP BY lat_grid, lng_grid
            HAVING event_count > 5
        """, (date,))
        
        # batchinsert
        updated = 0
        for g in grids:
            grid_id = f"LAT_{g['lat_grid']}_LNG_{g['lng_grid']}"
            try:
                await self.pool.execute("""
                    INSERT INTO geo_heatmap_grid
                    (grid_id, lat_grid, lng_grid, date, event_count, 
                     conflict_sum, avg_goldstein, avg_tone)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    event_count = VALUES(event_count),
                    conflict_sum = VALUES(conflict_sum),
                    avg_goldstein = VALUES(avg_goldstein),
                    avg_tone = VALUES(avg_tone)
                """, (
                    grid_id, g['lat_grid'], g['lng_grid'], date,
                    g['event_count'], g['conflict_sum'], 
                    g['avg_goldstein'], g['avg_tone']
                ))
                updated += 1
            except Exception as e:
                logger.warning(f"    skipgrid {grid_id}: {e}")
        
        logger.info(f"  ✓ update {updated} grid")
    
    async def _identify_hot_events(self, date: str):
        """Replace the day's hot list with the top fingerprints (needs step 3 first)."""
        logger.info(f"🔥 identify hot events: {date}")
        affected = await self.pool.execute(HOT_FINGERPRINTS_SQL, (date, date))
        logger.info(f"  ✓ daily_summary rows updated: {affected}")


async def main():
    """maininputmouth"""
    # parsearguments
    target_date = None
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
        # validatedateformat
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            print(f"❌ dateformaterror: {target_date}")
            print("   correctconfirmformat: YYYY-MM-DD")
            sys.exit(1)
    
    # runETL
    pipeline = GDELTETLPipeline()
    try:
        await pipeline.initialize()
        await pipeline.run_daily_etl(target_date)
    finally:
        await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
