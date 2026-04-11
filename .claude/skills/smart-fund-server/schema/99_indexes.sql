-- AUTO-GENERATED from pg_dump
-- 来源: jettask 生产库 (119.23.227.187:5432/jettask)
-- 重构方案: docs/3. 实施方案/3. 系统重构/06-ORM与DDD重构方案.md
--
-- 不要手动编辑;如需更新,跑 schema/regenerate.sh

SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET client_min_messages = warning;
SET row_security = off;
SET default_tablespace = '';
SET default_table_access_method = heap;


-- CONSTRAINT: ft_alipay_decisions ft_alipay_decisions_pkey
--
-- Name: ft_alipay_decisions ft_alipay_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_alipay_decisions
    ADD CONSTRAINT ft_alipay_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_alipay_positions ft_alipay_positions_fund_name_snapshot_date_key
--
-- Name: ft_alipay_positions ft_alipay_positions_fund_name_snapshot_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_alipay_positions
    ADD CONSTRAINT ft_alipay_positions_fund_name_snapshot_date_key UNIQUE (fund_name, snapshot_date);

-- CONSTRAINT: ft_alipay_positions ft_alipay_positions_pkey
--
-- Name: ft_alipay_positions ft_alipay_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_alipay_positions
    ADD CONSTRAINT ft_alipay_positions_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_cache ft_cache_fund_code_data_type_key
--
-- Name: ft_cache ft_cache_fund_code_data_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_cache
    ADD CONSTRAINT ft_cache_fund_code_data_type_key UNIQUE (fund_code, data_type);

-- CONSTRAINT: ft_cache ft_cache_pkey
--
-- Name: ft_cache ft_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_cache
    ADD CONSTRAINT ft_cache_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_collection_state ft_collection_state_aggregator_source_name_key
--
-- Name: ft_collection_state ft_collection_state_aggregator_source_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_collection_state
    ADD CONSTRAINT ft_collection_state_aggregator_source_name_key UNIQUE (aggregator, source_name);

-- CONSTRAINT: ft_collection_state ft_collection_state_pkey
--
-- Name: ft_collection_state ft_collection_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_collection_state
    ADD CONSTRAINT ft_collection_state_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_config ft_config_pkey
--
-- Name: ft_config ft_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_config
    ADD CONSTRAINT ft_config_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_decisions ft_decisions_pkey
--
-- Name: ft_decisions ft_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_decisions
    ADD CONSTRAINT ft_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_event_streams ft_event_streams_pkey
--
-- Name: ft_event_streams ft_event_streams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_event_streams
    ADD CONSTRAINT ft_event_streams_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_events ft_events_fingerprint_key
--
-- Name: ft_events ft_events_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_events
    ADD CONSTRAINT ft_events_fingerprint_key UNIQUE (fingerprint);

-- CONSTRAINT: ft_events ft_events_pkey
--
-- Name: ft_events ft_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_events
    ADD CONSTRAINT ft_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_fund_limits ft_fund_limits_fund_code_key
--
-- Name: ft_fund_limits ft_fund_limits_fund_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_fund_limits
    ADD CONSTRAINT ft_fund_limits_fund_code_key UNIQUE (fund_code);

-- CONSTRAINT: ft_fund_limits ft_fund_limits_pkey
--
-- Name: ft_fund_limits ft_fund_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_fund_limits
    ADD CONSTRAINT ft_fund_limits_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_index_fund ft_index_fund_index_code_fund_code_key
--
-- Name: ft_index_fund ft_index_fund_index_code_fund_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_index_fund
    ADD CONSTRAINT ft_index_fund_index_code_fund_code_key UNIQUE (index_code, fund_code);

-- CONSTRAINT: ft_index_fund ft_index_fund_pkey
--
-- Name: ft_index_fund ft_index_fund_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_index_fund
    ADD CONSTRAINT ft_index_fund_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_industry_index ft_industry_index_industry_id_index_code_key
--
-- Name: ft_industry_index ft_industry_index_industry_id_index_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_index
    ADD CONSTRAINT ft_industry_index_industry_id_index_code_key UNIQUE (industry_id, index_code);

-- CONSTRAINT: ft_industry_index ft_industry_index_pkey
--
-- Name: ft_industry_index ft_industry_index_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_index
    ADD CONSTRAINT ft_industry_index_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_industry_mapping ft_industry_mapping_industry_id_key
--
-- Name: ft_industry_mapping ft_industry_mapping_industry_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_mapping
    ADD CONSTRAINT ft_industry_mapping_industry_id_key UNIQUE (industry_id);

-- CONSTRAINT: ft_industry_mapping ft_industry_mapping_pkey
--
-- Name: ft_industry_mapping ft_industry_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_mapping
    ADD CONSTRAINT ft_industry_mapping_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_lessons ft_lessons_pkey
--
-- Name: ft_lessons ft_lessons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_lessons
    ADD CONSTRAINT ft_lessons_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_macro_indicators ft_macro_indicators_indicator_period_source_key
--
-- Name: ft_macro_indicators ft_macro_indicators_indicator_period_source_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_macro_indicators
    ADD CONSTRAINT ft_macro_indicators_indicator_period_source_key UNIQUE (indicator, period, source);

-- CONSTRAINT: ft_macro_indicators ft_macro_indicators_pkey
--
-- Name: ft_macro_indicators ft_macro_indicators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_macro_indicators
    ADD CONSTRAINT ft_macro_indicators_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_market_cache ft_market_cache_data_type_key
--
-- Name: ft_market_cache ft_market_cache_data_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_market_cache
    ADD CONSTRAINT ft_market_cache_data_type_key UNIQUE (data_type);

-- CONSTRAINT: ft_market_cache ft_market_cache_pkey
--
-- Name: ft_market_cache ft_market_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_market_cache
    ADD CONSTRAINT ft_market_cache_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_market_flow ft_market_flow_pkey
--
-- Name: ft_market_flow ft_market_flow_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_market_flow
    ADD CONSTRAINT ft_market_flow_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_news ft_news_pkey
--
-- Name: ft_news ft_news_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_news
    ADD CONSTRAINT ft_news_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_pending_decisions ft_pending_decisions_pkey
--
-- Name: ft_pending_decisions ft_pending_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_pending_decisions
    ADD CONSTRAINT ft_pending_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_positions ft_positions_fund_code_key
--
-- Name: ft_positions ft_positions_fund_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_positions
    ADD CONSTRAINT ft_positions_fund_code_key UNIQUE (fund_code);

-- CONSTRAINT: ft_positions ft_positions_pkey
--
-- Name: ft_positions ft_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_positions
    ADD CONSTRAINT ft_positions_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_raw_data ft_raw_data_pkey
--
-- Name: ft_raw_data ft_raw_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data
    ADD CONSTRAINT ft_raw_data_pkey PRIMARY KEY (id, fetched_at);

-- CONSTRAINT: ft_raw_data_202603 ft_raw_data_202603_pkey
--
-- Name: ft_raw_data_202603 ft_raw_data_202603_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data_202603
    ADD CONSTRAINT ft_raw_data_202603_pkey PRIMARY KEY (id, fetched_at);

-- CONSTRAINT: ft_raw_data_202604 ft_raw_data_202604_pkey
--
-- Name: ft_raw_data_202604 ft_raw_data_202604_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data_202604
    ADD CONSTRAINT ft_raw_data_202604_pkey PRIMARY KEY (id, fetched_at);

-- CONSTRAINT: ft_raw_data_202605 ft_raw_data_202605_pkey
--
-- Name: ft_raw_data_202605 ft_raw_data_202605_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data_202605
    ADD CONSTRAINT ft_raw_data_202605_pkey PRIMARY KEY (id, fetched_at);

-- CONSTRAINT: ft_raw_data_202606 ft_raw_data_202606_pkey
--
-- Name: ft_raw_data_202606 ft_raw_data_202606_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data_202606
    ADD CONSTRAINT ft_raw_data_202606_pkey PRIMARY KEY (id, fetched_at);

-- CONSTRAINT: ft_reviews ft_reviews_pkey
--
-- Name: ft_reviews ft_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_reviews
    ADD CONSTRAINT ft_reviews_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_run_log ft_run_log_pkey
--
-- Name: ft_run_log ft_run_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_run_log
    ADD CONSTRAINT ft_run_log_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_sentiment ft_sentiment_pkey
--
-- Name: ft_sentiment ft_sentiment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_sentiment
    ADD CONSTRAINT ft_sentiment_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_signals ft_signals_pkey
--
-- Name: ft_signals ft_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_signals
    ADD CONSTRAINT ft_signals_pkey PRIMARY KEY (id);

-- CONSTRAINT: ft_trades ft_trades_pkey
--
-- Name: ft_trades ft_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_trades
    ADD CONSTRAINT ft_trades_pkey PRIMARY KEY (id);

-- INDEX: idx_raw_domain_time
--
-- Name: idx_raw_domain_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_domain_time ON ONLY public.ft_raw_data USING btree (data_domain, fetched_at);

-- INDEX: ft_raw_data_202603_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202603_data_domain_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202603_data_domain_fetched_at_idx ON public.ft_raw_data_202603 USING btree (data_domain, fetched_at);

-- INDEX: idx_raw_cache
--
-- Name: idx_raw_cache; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_cache ON ONLY public.ft_raw_data USING btree (source, method, params_hash, fetched_at DESC);

-- INDEX: ft_raw_data_202603_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202603_source_method_params_hash_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202603_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202603 USING btree (source, method, params_hash, fetched_at DESC);

-- INDEX: idx_raw_trade_date
--
-- Name: idx_raw_trade_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_trade_date ON ONLY public.ft_raw_data USING btree (trade_date) WHERE (trade_date IS NOT NULL);

-- INDEX: ft_raw_data_202603_trade_date_idx
--
-- Name: ft_raw_data_202603_trade_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202603_trade_date_idx ON public.ft_raw_data_202603 USING btree (trade_date) WHERE (trade_date IS NOT NULL);

-- INDEX: ft_raw_data_202604_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202604_data_domain_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202604_data_domain_fetched_at_idx ON public.ft_raw_data_202604 USING btree (data_domain, fetched_at);

-- INDEX: ft_raw_data_202604_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202604_source_method_params_hash_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202604_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202604 USING btree (source, method, params_hash, fetched_at DESC);

-- INDEX: ft_raw_data_202604_trade_date_idx
--
-- Name: ft_raw_data_202604_trade_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202604_trade_date_idx ON public.ft_raw_data_202604 USING btree (trade_date) WHERE (trade_date IS NOT NULL);

-- INDEX: ft_raw_data_202605_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202605_data_domain_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202605_data_domain_fetched_at_idx ON public.ft_raw_data_202605 USING btree (data_domain, fetched_at);

-- INDEX: ft_raw_data_202605_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202605_source_method_params_hash_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202605_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202605 USING btree (source, method, params_hash, fetched_at DESC);

-- INDEX: ft_raw_data_202605_trade_date_idx
--
-- Name: ft_raw_data_202605_trade_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202605_trade_date_idx ON public.ft_raw_data_202605 USING btree (trade_date) WHERE (trade_date IS NOT NULL);

-- INDEX: ft_raw_data_202606_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202606_data_domain_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202606_data_domain_fetched_at_idx ON public.ft_raw_data_202606 USING btree (data_domain, fetched_at);

-- INDEX: ft_raw_data_202606_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202606_source_method_params_hash_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202606_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202606 USING btree (source, method, params_hash, fetched_at DESC);

-- INDEX: ft_raw_data_202606_trade_date_idx
--
-- Name: ft_raw_data_202606_trade_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ft_raw_data_202606_trade_date_idx ON public.ft_raw_data_202606 USING btree (trade_date) WHERE (trade_date IS NOT NULL);

-- INDEX: idx_ft_alipay_date
--
-- Name: idx_ft_alipay_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_alipay_date ON public.ft_alipay_positions USING btree (snapshot_date);

-- INDEX: idx_ft_alipay_decisions_date
--
-- Name: idx_ft_alipay_decisions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_alipay_decisions_date ON public.ft_alipay_decisions USING btree (decision_date);

-- INDEX: idx_ft_alipay_name
--
-- Name: idx_ft_alipay_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_alipay_name ON public.ft_alipay_positions USING btree (fund_name);

-- INDEX: idx_ft_cache_lookup
--
-- Name: idx_ft_cache_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_cache_lookup ON public.ft_cache USING btree (fund_code, data_type);

-- INDEX: idx_ft_collection_state_enabled
--
-- Name: idx_ft_collection_state_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_collection_state_enabled ON public.ft_collection_state USING btree (enabled, aggregator);

-- INDEX: idx_ft_decisions_date
--
-- Name: idx_ft_decisions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_decisions_date ON public.ft_decisions USING btree (decision_date);

-- INDEX: idx_ft_event_streams_industry
--
-- Name: idx_ft_event_streams_industry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_event_streams_industry ON public.ft_event_streams USING btree (industry, last_event_at DESC);

-- INDEX: idx_ft_event_streams_state
--
-- Name: idx_ft_event_streams_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_event_streams_state ON public.ft_event_streams USING btree (state, momentum);

-- INDEX: idx_ft_events_companies
--
-- Name: idx_ft_events_companies; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_events_companies ON public.ft_events USING gin (companies);

-- INDEX: idx_ft_events_event_time
--
-- Name: idx_ft_events_event_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_events_event_time ON public.ft_events USING btree (event_time DESC);

-- INDEX: idx_ft_events_industries
--
-- Name: idx_ft_events_industries; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_events_industries ON public.ft_events USING gin (industries);

-- INDEX: idx_ft_events_type
--
-- Name: idx_ft_events_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_events_type ON public.ft_events USING btree (event_type, event_time);

-- INDEX: idx_ft_fund_limits_code
--
-- Name: idx_ft_fund_limits_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_fund_limits_code ON public.ft_fund_limits USING btree (fund_code);

-- INDEX: idx_ft_index_fund_index
--
-- Name: idx_ft_index_fund_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_index_fund_index ON public.ft_index_fund USING btree (index_code);

-- INDEX: idx_ft_industry_keywords
--
-- Name: idx_ft_industry_keywords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_industry_keywords ON public.ft_industry_mapping USING gin (keywords);

-- INDEX: idx_ft_lessons_category
--
-- Name: idx_ft_lessons_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_lessons_category ON public.ft_lessons USING btree (category);

-- INDEX: idx_ft_lessons_status
--
-- Name: idx_ft_lessons_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_lessons_status ON public.ft_lessons USING btree (status);

-- INDEX: idx_ft_macro_ind_period
--
-- Name: idx_ft_macro_ind_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_macro_ind_period ON public.ft_macro_indicators USING btree (indicator, period);

-- INDEX: idx_ft_market_cache_type
--
-- Name: idx_ft_market_cache_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_market_cache_type ON public.ft_market_cache USING btree (data_type);

-- INDEX: idx_ft_market_flow_type_date
--
-- Name: idx_ft_market_flow_type_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_market_flow_type_date ON public.ft_market_flow USING btree (data_type, trade_date);

-- INDEX: idx_ft_news_category
--
-- Name: idx_ft_news_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_news_category ON public.ft_news USING btree (category, published_at);

-- INDEX: idx_ft_news_extracted
--
-- Name: idx_ft_news_extracted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_news_extracted ON public.ft_news USING btree (event_extracted, published_at DESC) WHERE (event_extracted = false);

-- INDEX: idx_ft_news_fingerprint
--
-- Name: idx_ft_news_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_ft_news_fingerprint ON public.ft_news USING btree (fingerprint);

-- INDEX: idx_ft_news_source_time
--
-- Name: idx_ft_news_source_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_news_source_time ON public.ft_news USING btree (source, published_at);

-- INDEX: idx_ft_pending_date
--
-- Name: idx_ft_pending_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_pending_date ON public.ft_pending_decisions USING btree (decision_date);

-- INDEX: idx_ft_pending_event_stream
--
-- Name: idx_ft_pending_event_stream; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_pending_event_stream ON public.ft_pending_decisions USING btree (event_stream_id);

-- INDEX: idx_ft_pending_source
--
-- Name: idx_ft_pending_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_pending_source ON public.ft_pending_decisions USING btree (decision_source, status);

-- INDEX: idx_ft_pending_status
--
-- Name: idx_ft_pending_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_pending_status ON public.ft_pending_decisions USING btree (status);

-- INDEX: idx_ft_reviews_date
--
-- Name: idx_ft_reviews_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_reviews_date ON public.ft_reviews USING btree (decision_date);

-- INDEX: idx_ft_reviews_outcome
--
-- Name: idx_ft_reviews_outcome; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_reviews_outcome ON public.ft_reviews USING btree (outcome);

-- INDEX: idx_ft_sentiment_type_date
--
-- Name: idx_ft_sentiment_type_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_sentiment_type_date ON public.ft_sentiment USING btree (data_type, trade_date);

-- INDEX: idx_ft_signals_date
--
-- Name: idx_ft_signals_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_signals_date ON public.ft_signals USING btree (fund_code, signal_date);

-- INDEX: idx_ft_trades_date
--
-- Name: idx_ft_trades_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_trades_date ON public.ft_trades USING btree (fund_code, trade_date);

-- INDEX: idx_ft_trades_dry_run
--
-- Name: idx_ft_trades_dry_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ft_trades_dry_run ON public.ft_trades USING btree (dry_run, trade_date);

-- INDEX: uq_ft_pending_event_fund_date
--
-- Name: uq_ft_pending_event_fund_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ft_pending_event_fund_date ON public.ft_pending_decisions USING btree (event_stream_id, fund_code, decision_date) WHERE ((decision_source)::text = 'event_driven'::text);

-- INDEX: uq_ft_reviews_dec_fund
--
-- Name: uq_ft_reviews_dec_fund; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ft_reviews_dec_fund ON public.ft_reviews USING btree (decision_id, fund_code) WHERE (decision_id IS NOT NULL);

-- INDEX: uq_ft_reviews_paper
--
-- Name: uq_ft_reviews_paper; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ft_reviews_paper ON public.ft_reviews USING btree (fund_code, decision_date, decision_source, decision_action) WHERE (decision_id IS NULL);

-- INDEX: uq_market_flow_dragon
--
-- Name: uq_market_flow_dragon; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_flow_dragon ON public.ft_market_flow USING btree (((data ->> 'source'::text)), ((data ->> 'code'::text)), trade_date) WHERE ((data_type)::text = 'dragon_tiger'::text);

-- INDEX: uq_market_flow_northbound
--
-- Name: uq_market_flow_northbound; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_flow_northbound ON public.ft_market_flow USING btree (((data ->> 'mutual_type'::text)), trade_date) WHERE ((data_type)::text = 'northbound'::text);

-- INDEX: uq_market_flow_sector
--
-- Name: uq_market_flow_sector; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_flow_sector ON public.ft_market_flow USING btree (((data ->> 'name'::text)), trade_date) WHERE ((data_type)::text = 'sector_flow'::text);

-- INDEX: uq_market_flow_stock
--
-- Name: uq_market_flow_stock; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_market_flow_stock ON public.ft_market_flow USING btree (((data ->> 'code'::text)), trade_date) WHERE ((data_type)::text = 'stock_flow'::text);

-- INDEX ATTACH: ft_raw_data_202603_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202603_data_domain_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_domain_time ATTACH PARTITION public.ft_raw_data_202603_data_domain_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202603_pkey
--
-- Name: ft_raw_data_202603_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ft_raw_data_pkey ATTACH PARTITION public.ft_raw_data_202603_pkey;

-- INDEX ATTACH: ft_raw_data_202603_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202603_source_method_params_hash_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_cache ATTACH PARTITION public.ft_raw_data_202603_source_method_params_hash_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202603_trade_date_idx
--
-- Name: ft_raw_data_202603_trade_date_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_trade_date ATTACH PARTITION public.ft_raw_data_202603_trade_date_idx;

-- INDEX ATTACH: ft_raw_data_202604_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202604_data_domain_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_domain_time ATTACH PARTITION public.ft_raw_data_202604_data_domain_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202604_pkey
--
-- Name: ft_raw_data_202604_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ft_raw_data_pkey ATTACH PARTITION public.ft_raw_data_202604_pkey;

-- INDEX ATTACH: ft_raw_data_202604_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202604_source_method_params_hash_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_cache ATTACH PARTITION public.ft_raw_data_202604_source_method_params_hash_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202604_trade_date_idx
--
-- Name: ft_raw_data_202604_trade_date_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_trade_date ATTACH PARTITION public.ft_raw_data_202604_trade_date_idx;

-- INDEX ATTACH: ft_raw_data_202605_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202605_data_domain_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_domain_time ATTACH PARTITION public.ft_raw_data_202605_data_domain_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202605_pkey
--
-- Name: ft_raw_data_202605_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ft_raw_data_pkey ATTACH PARTITION public.ft_raw_data_202605_pkey;

-- INDEX ATTACH: ft_raw_data_202605_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202605_source_method_params_hash_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_cache ATTACH PARTITION public.ft_raw_data_202605_source_method_params_hash_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202605_trade_date_idx
--
-- Name: ft_raw_data_202605_trade_date_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_trade_date ATTACH PARTITION public.ft_raw_data_202605_trade_date_idx;

-- INDEX ATTACH: ft_raw_data_202606_data_domain_fetched_at_idx
--
-- Name: ft_raw_data_202606_data_domain_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_domain_time ATTACH PARTITION public.ft_raw_data_202606_data_domain_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202606_pkey
--
-- Name: ft_raw_data_202606_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ft_raw_data_pkey ATTACH PARTITION public.ft_raw_data_202606_pkey;

-- INDEX ATTACH: ft_raw_data_202606_source_method_params_hash_fetched_at_idx
--
-- Name: ft_raw_data_202606_source_method_params_hash_fetched_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_cache ATTACH PARTITION public.ft_raw_data_202606_source_method_params_hash_fetched_at_idx;

-- INDEX ATTACH: ft_raw_data_202606_trade_date_idx
--
-- Name: ft_raw_data_202606_trade_date_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_raw_trade_date ATTACH PARTITION public.ft_raw_data_202606_trade_date_idx;


--
-- PostgreSQL database dump complete
