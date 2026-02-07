"""
title: PostgreSQL Financial Data Query Tool
author: Ashley
version: 0.1.0
description: Query the document_store PostgreSQL database for financial data, quant scores, earnings, economic indicators, and mining rankings.
"""

import json
import os
from typing import Optional


class Tools:
    class Valves:
        """Configuration for database connection."""
        DB_HOST: str = "host.docker.internal"
        DB_PORT: int = 5432
        DB_NAME: str = "document_store"
        DB_USER: str = "doc_admin"
        DB_PASSWORD: str = "Sw116dsSw116ds!"

    def __init__(self):
        self.valves = self.Valves()

    def _get_connection(self):
        import psycopg2
        return psycopg2.connect(
            host=self.valves.DB_HOST,
            port=self.valves.DB_PORT,
            database=self.valves.DB_NAME,
            user=self.valves.DB_USER,
            password=self.valves.DB_PASSWORD,
        )

    def _run_query(self, sql: str, params: tuple = ()) -> str:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            if not rows:
                return "No results found."
            results = []
            for row in rows:
                row_dict = {}
                for col, val in zip(columns, row):
                    if val is None:
                        row_dict[col] = None
                    elif isinstance(val, (int, float)):
                        row_dict[col] = val
                    else:
                        row_dict[col] = str(val)
                results.append(row_dict)
            return json.dumps(results, indent=2, default=str)
        finally:
            conn.close()

    def get_company_fundamentals(self, ticker: str) -> str:
        """
        Get comprehensive financial fundamentals for a company including valuation (PE, P/FCF), profitability (ROIC, ROE, margins), growth (revenue/FCF CAGRs), debt metrics, and stock returns (1M through 10Y). This is the canonical source for stock price returns.

        :param ticker: Stock ticker symbol (e.g., NVDA, AAPL, MSFT)
        :return: JSON with key financial metrics
        """
        sql = """
            SELECT ticker, name, sector, industry, market_cap, last_price,
                   total_return_1m, total_return_3m, total_return_6m,
                   total_return_1y, total_return_3y, total_return_5y, total_return_10y,
                   pe_ltm, pe_ntm, p_fcf_ltm, ev_ebitda_ltm, ev_ebitda_ntm,
                   fcf_ltm, fcf_margin_pct_ltm, fcf_cagr_1y, fcf_cagr_3y, fcf_cagr_5y,
                   roic_fy, roic_3yavg, roe_pct_ltm,
                   ebit_margin_pct_ltm, gross_margin_pct_ltm,
                   total_revenues_cagr_1y, total_revenues_cagr_3y, total_revenues_cagr_5y,
                   net_debt_ltm, total_debt_equity_ltm,
                   dividend_yield_ltm, shares_outstanding
            FROM company_fundamentals
            WHERE UPPER(ticker) = UPPER(%s)
        """
        return self._run_query(sql, (ticker,))

    def get_quality_score(self, ticker: str) -> str:
        """
        Get quality ranking and scores for a company. Quality score uses 1-8 scale where 1 is BEST and 8 is WORST.

        :param ticker: Stock ticker symbol (e.g., NVDA, AAPL)
        :return: JSON with quality score, component scores, and ranking
        """
        sql = """
            SELECT ticker, name, sector, industry, market_cap,
                   global_rank, quality_score, cash_flow_score, growth_score, risk_score,
                   earnings_momentum,
                   pe_ntm, fcf_ev_yield_ltm, p_fcf_ltm, ev_sales_ntm, peg_ntm,
                   total_return_3m, total_return_1y
            FROM company_quality_scores
            WHERE UPPER(ticker) = UPPER(%s)
        """
        return self._run_query(sql, (ticker,))

    def get_top_ranked_companies(self, limit: int = 20) -> str:
        """
        Get the top ranked companies by quality score. Lower quality_score = better. Scale is 1 (best) to 8 (worst).

        :param limit: Number of companies to return (default 20, max 50)
        :return: JSON array of top-ranked companies
        """
        limit = min(limit, 50)
        sql = """
            SELECT ticker, name, sector, industry, market_cap,
                   global_rank, quality_score, cash_flow_score, growth_score, risk_score,
                   earnings_momentum, pe_ntm, total_return_1y
            FROM company_quality_scores
            WHERE quality_score IS NOT NULL
            ORDER BY quality_score ASC
            LIMIT %s
        """
        return self._run_query(sql, (limit,))

    def get_earnings_data(self, ticker: str) -> str:
        """
        Get earnings data for a company including next earnings date, revenue/EPS estimates, and analyst revisions.

        :param ticker: Stock ticker symbol
        :return: JSON with earnings data
        """
        sql = """
            SELECT ticker, name, sector, industry, market_cap,
                   next_earnings_date, next_earnings_when,
                   total_revenues_ltm, total_revenues_fq,
                   revenues_est_avg_fy1e, revenues_est_avg_fq1e,
                   eps_est_avg_fy1e, eps_est_avg_fq1e
            FROM company_earnings
            WHERE UPPER(ticker) = UPPER(%s)
        """
        return self._run_query(sql, (ticker,))

    def get_economic_indicators(self, country: str, category: Optional[str] = None, limit: int = 20) -> str:
        """
        Get economic indicator data for a country. Available countries: USA, China, Japan, Germany, UK, etc. Available categories: INFLATION, EMPLOYMENT, RETAIL SALES, CONSUMER CONFIDENCE, GDP, etc.

        :param country: Country name (e.g., USA, China, Japan, Germany)
        :param category: Optional category filter (e.g., INFLATION, EMPLOYMENT)
        :param limit: Number of most recent records per indicator (default 20)
        :return: JSON with economic data
        """
        if category:
            sql = """
                SELECT country, category, indicator_name, bloomberg_ticker, date, value
                FROM economic_indicators
                WHERE UPPER(country) = UPPER(%s) AND UPPER(category) = UPPER(%s)
                ORDER BY date DESC
                LIMIT %s
            """
            return self._run_query(sql, (country, category, limit))
        else:
            sql = """
                SELECT DISTINCT country, category, indicator_name, bloomberg_ticker
                FROM economic_indicators
                WHERE UPPER(country) = UPPER(%s)
                ORDER BY category, indicator_name
            """
            return self._run_query(sql, (country,))

    def get_miner_rankings(self, ticker: Optional[str] = None, limit: int = 20) -> str:
        """
        Get mining company quality rankings. Lower mining_quality_score = better. Includes survival, capital discipline, balance sheet, and market recognition scores.

        :param ticker: Optional specific ticker. If not provided, returns top-ranked miners.
        :param limit: Number of miners to return if no ticker specified (default 20)
        :return: JSON with mining rankings
        """
        if ticker:
            sql = """
                SELECT ticker, name, country, market_cap,
                       mining_quality_score, survival_score, capital_discipline_score,
                       balance_sheet_score, market_recognition_score
                FROM company_miner_rankings
                WHERE UPPER(ticker) = UPPER(%s)
            """
            return self._run_query(sql, (ticker,))
        else:
            sql = """
                SELECT ticker, name, country, market_cap,
                       mining_quality_score, survival_score, capital_discipline_score,
                       balance_sheet_score, market_recognition_score
                FROM company_miner_rankings
                WHERE mining_quality_score IS NOT NULL
                ORDER BY mining_quality_score ASC
                LIMIT %s
            """
            return self._run_query(sql, (limit,))

    def search_companies_by_sector(self, sector: str, limit: int = 20) -> str:
        """
        Search for companies by sector. Returns quality scores and key metrics. Common sectors: Technology, Healthcare, Industrials, Consumer Discretionary, Energy, Financials, Materials, etc.

        :param sector: Sector name or partial match (e.g., Technology, Industrial)
        :param limit: Max results (default 20)
        :return: JSON array of matching companies
        """
        sql = """
            SELECT ticker, name, sector, industry, market_cap,
                   quality_score, global_rank, pe_ntm, total_return_1y
            FROM company_quality_scores
            WHERE LOWER(sector) LIKE LOWER(%s)
            ORDER BY quality_score ASC NULLS LAST
            LIMIT %s
        """
        return self._run_query(sql, (f"%{sector}%", limit))

    def run_custom_query(self, sql_query: str) -> str:
        """
        Run a custom read-only SQL query against the financial database. Only SELECT queries are allowed. Available tables: company_fundamentals, company_quality_scores, company_earnings, company_technical, company_finance, company_percentiles, company_13f_positions, fund_13f_positions, company_miner_rankings, company_junior_miners, company_roic_summary, economic_indicators.

        :param sql_query: A SELECT SQL query to execute
        :return: JSON results
        """
        cleaned = sql_query.strip().rstrip(";").strip()
        if not cleaned.upper().startswith("SELECT"):
            return "Error: Only SELECT queries are allowed for safety."
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE"]
        upper_query = cleaned.upper()
        for keyword in dangerous:
            if keyword in upper_query.split():
                return f"Error: {keyword} operations are not allowed."
        return self._run_query(cleaned)

    def list_tables(self) -> str:
        """
        List all available tables in the financial database with their row counts.

        :return: JSON with table names and record counts
        """
        sql = """
            SELECT schemaname, tablename,
                   (SELECT count(*) FROM information_schema.columns c
                    WHERE c.table_name = t.tablename AND c.table_schema = t.schemaname) as column_count
            FROM pg_tables t
            WHERE schemaname = 'public'
            ORDER BY tablename
        """
        return self._run_query(sql)

    def get_table_columns(self, table_name: str) -> str:
        """
        Get all column names and types for a specific table. Useful for exploring available data before querying.

        :param table_name: Name of the table (e.g., company_fundamentals, economic_indicators)
        :return: JSON with column names and data types
        """
        sql = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position
        """
        return self._run_query(sql, (table_name,))
